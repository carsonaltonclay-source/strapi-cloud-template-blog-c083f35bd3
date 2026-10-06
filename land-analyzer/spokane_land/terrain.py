"""Is the parcel on a hill? Slope and landform from USGS 3DEP elevation.

Two requests to the national elevation service per parcel:
  * slope statistics + histogram inside the parcel (how steep, how much flat ground)
  * elevation samples at points inside the parcel and on rings around it
    (relief across the lot, and whether it sits high or low compared with its
    neighbourhood -> hilltop / hillside / valley)
"""

import json
import math

from . import geo
from .config import ELEVATION_IMAGE_SERVER
from .http import HttpError, get_json

FT_PER_M = 3.28084
FLAT_DEG = 8            # slopes under ~8 degrees (14%) are easy building ground
NEIGHBORHOOD_M = 800    # radius used to judge hilltop vs valley

# (upper bound in degrees, key, label)
SLOPE_CLASSES = [
    (3, "flat", "Flat"),
    (8, "gentle", "Gentle slope"),
    (15, "rolling", "Rolling / moderate slope"),
    (25, "steep", "Steep"),
    (91, "very_steep", "Very steep"),
]
POSITION_LABELS = {
    "hilltop": "On a hilltop / ridge",
    "hillside": "On a hillside",
    "valley": "In a valley / low ground",
    "level": "Level ground (no hill)",
    "mid": "Mid-slope",
}


def _stats(geometry, slope=False, pixel_m=10):
    params = {
        "geometry": json.dumps(geometry),
        "geometryType": "esriGeometryPolygon",
        "pixelSize": json.dumps({"x": pixel_m, "y": pixel_m, "spatialReference": {"wkid": 3857}}),
        "f": "json",
    }
    if slope:
        params["renderingRule"] = json.dumps({"rasterFunction": "Slope Degrees"})
    data = get_json(ELEVATION_IMAGE_SERVER + "/computeStatisticsHistograms", params, post=True)
    st = (data.get("statistics") or [None])[0]
    hist = (data.get("histograms") or [None])[0]
    return st, hist


def _samples(shape, clat, clon):
    """Elevations (m) at a grid inside the parcel and on 3 rings around it."""
    ring = max(shape["rings"], key=len)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    inside_pts = [(clon, clat)]
    for i in range(1, 6):
        for j in range(1, 6):
            x = min(xs) + (max(xs) - min(xs)) * i / 6
            y = min(ys) + (max(ys) - min(ys)) * j / 6
            if geo.point_in_polygon(x, y, shape["rings"]):
                inside_pts.append((x, y))
    around_pts = []
    for r_m in (300, 550, NEIGHBORHOOD_M):
        around_pts += geo.circle_polygon(clat, clon, r_m, 8)[:-1]
    pts = inside_pts + around_pts
    data = get_json(ELEVATION_IMAGE_SERVER + "/getSamples", {
        "geometry": json.dumps({"points": [[round(x, 6), round(y, 6)] for x, y in pts], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true", "f": "json",
    }, post=True)
    vals = {}
    for smp in data.get("samples", []):
        try:
            v = float(smp["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if v > -1000:
            vals[smp.get("locationId")] = v
    n_in = len(inside_pts)
    inside = [v for k, v in vals.items() if k is not None and k < n_in]
    around = [v for k, v in vals.items() if k is not None and k >= n_in]
    return inside, around


def _pixel_for(acres):
    """~10 m pixels for normal lots, coarser for huge ones (keeps requests fast)."""
    if not acres:
        return 10
    side_m = math.sqrt(acres * 4046.86)
    return max(10, round(side_m / 70))


def analyze_terrain(parcel_geometry, lat, lon, acres=None):
    """Return a terrain dict, or None if the elevation service can't answer."""
    if parcel_geometry and parcel_geometry.get("rings"):
        shape = {"rings": parcel_geometry["rings"], "spatialReference": {"wkid": 4326}}
        clat, clon = geo.polygon_centroid(parcel_geometry["rings"])
    elif lat is not None and lon is not None:
        # No parcel outline: look at a ~1-acre circle around the map pin.
        shape = {"rings": [geo.circle_polygon(lat, lon, 36, 16)], "spatialReference": {"wkid": 4326}}
        clat, clon = lat, lon
    else:
        return None

    px = _pixel_for(acres)
    try:
        slope, hist = _stats(shape, slope=True, pixel_m=px)
        inside, around = _samples(shape, clat, clon)
    except HttpError:
        return None
    if not slope or slope.get("count", 0) == 0 or not inside:
        return None
    elev = {"mean": sum(inside) / len(inside), "min": min(inside), "max": max(inside)}
    around = ({"mean": sum(around) / len(around), "min": min(around + inside), "max": max(around + inside),
               "count": len(around)} if around else None)

    mean_slope = slope["mean"]
    flat_pct = None
    if hist and hist.get("counts"):
        lo, counts = hist["min"], hist["counts"]
        width = (hist["max"] - lo) / max(len(counts), 1)
        flat = sum(c for i, c in enumerate(counts) if lo + (i + 1) * width <= FLAT_DEG + 0.5)
        flat_pct = round(100 * flat / max(sum(counts), 1))

    for upper, key, label in SLOPE_CLASSES:
        if mean_slope < upper:
            slope_key, slope_label = key, label
            break

    relief_ft = (elev["max"] - elev["min"]) * FT_PER_M
    position = "mid"
    tpi_ft = None
    if around and around.get("count"):
        rng = around["max"] - around["min"]
        tpi_ft = (elev["mean"] - around["mean"]) * FT_PER_M
        rel = (elev["mean"] - around["min"]) / rng if rng > 0 else 0.5
        if rng * FT_PER_M < 40 and mean_slope < 5:
            position = "level"
        elif rel >= 0.72 and tpi_ft > 25:
            position = "hilltop"
        elif rel <= 0.28 and tpi_ft < -25:
            position = "valley"
        elif mean_slope >= 8:
            position = "hillside"
        elif mean_slope < 5:
            position = "level"
    elif mean_slope < 5:
        position = "level"

    on_hill = position in ("hilltop", "hillside") or mean_slope >= 8
    return {
        "on_hill": on_hill,
        "position": position,
        "position_label": POSITION_LABELS[position],
        "slope_class": slope_key,
        "slope_label": slope_label,
        "slope_mean_deg": round(mean_slope, 1),
        "slope_max_deg": round(slope["max"], 1),
        "slope_mean_pct": round(math.tan(math.radians(mean_slope)) * 100),
        "flat_pct": flat_pct,
        "relief_ft": round(relief_ft),
        "elevation_ft": round(elev["mean"] * FT_PER_M),
        "above_surroundings_ft": round(tpi_ft) if tpi_ft is not None else None,
        "source": "USGS 3DEP elevation",
    }
