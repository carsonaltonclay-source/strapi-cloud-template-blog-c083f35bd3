"""Where on the parcel could a house actually go?

Samples a grid of points across the parcel and keeps the ones that are:
  * inside the zoning setbacks (from the road and from the other lot lines),
  * outside mapped wetlands, FEMA flood zones, landslides and stream buffers,
  * under 30% slope (slope from a USGS 3DEP elevation grid).

Reports the buildable area, the best house site (gentlest ground, then
closest to the road), the driveway length from the road to that site and the
slope there (for grading cost).
"""

import json
import math

from . import arcgis, geo
from .config import ELEVATION_IMAGE_SERVER, LAYERS
from .http import HttpError, get_json

FT = 0.3048
HALF_ROW_M = 30 * FT          # road centerline to right-of-way line, typical rural county road
MAX_SLOPE_PCT = 30
GOOD_SLOPE_PCT = 15
GRID_N = 18                   # 18 x 18 grid over the parcel's bounding box


def _local(lat0):
    ky = 111320.0
    kx = 111320.0 * math.cos(math.radians(lat0))
    return kx, ky


def _hazard_polys(shape, state, in_spokane):
    """Polygons (lon/lat rings) where a house can't go, and the layers that didn't answer.
    Uses the deal-breaker checks' exact queries, so these come from the HTTP cache."""
    from .dealbreakers import hazard_query
    layers = ["nwi_wetlands", "fema_flood"] + (["wa_landslides"] if state == "WA" else []) + \
             (["sc_stream_buffers"] if in_spokane else [])
    polys, missing = [], []
    for layer in layers:
        try:
            feats = hazard_query(layer, shape, None, None)
        except HttpError:
            missing.append({"nwi_wetlands": "wetlands", "fema_flood": "flood zones", "wa_landslides": "landslides",
                            "sc_stream_buffers": "stream buffers"}.get(layer, layer))
            continue
        polys += [f["geometry"]["rings"] for f in feats if (f.get("geometry") or {}).get("rings")]
    return polys, missing


def _elevations(points):
    try:
        data = get_json(ELEVATION_IMAGE_SERVER + "/getSamples", {
            "geometry": json.dumps({"points": [[round(x, 6), round(y, 6)] for x, y in points],
                                    "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true", "f": "json",
        }, post=True)
    except HttpError:
        return None
    out = [None] * len(points)
    for s in data.get("samples", []):
        try:
            v = float(s["value"])
        except (KeyError, TypeError, ValueError):
            continue
        k = s.get("locationId")
        if k is not None and 0 <= k < len(out) and v > -1000:
            out[k] = v
    return out


def building_site(shape, roads, setbacks, state=None, in_spokane=False):
    """setbacks: {"front_ft", "side_ft"}; roads: [{"paths", "dist_m", ...}] from Enricher.roads."""
    if not shape or not shape.get("rings"):
        return None
    rings = shape["rings"]
    lat0, lon0 = geo.polygon_centroid(rings)
    kx, ky = _local(lat0)
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    w_m, h_m = (max(xs) - min(xs)) * kx, (max(ys) - min(ys)) * ky
    step_m = max(3.0 if max(w_m, h_m) < 60 else 6.0, max(w_m, h_m) / GRID_N)  # finer on town lots
    nx, ny = max(2, int(w_m / step_m) + 1), max(2, int(h_m / step_m) + 1)
    grid = [(min(xs) + (i + 0.5) * step_m / kx, min(ys) + (j + 0.5) * step_m / ky) for j in range(ny) for i in range(nx)]
    elev = _elevations(grid)
    if elev is None:
        return None

    def z(i, j):
        if 0 <= i < nx and 0 <= j < ny:
            return elev[j * nx + i]
        return None

    def slope_pct(i, j):
        c = z(i, j)
        if c is None:
            return None
        gx = [(z(i + 1, j), 1), (z(i - 1, j), -1)]
        gy = [(z(i, j + 1), 1), (z(i, j - 1), -1)]
        dx = [s * (v - c) for v, s in gx if v is not None]
        dy = [s * (v - c) for v, s in gy if v is not None]
        if not dx or not dy:
            return None
        return math.hypot(sum(dx) / len(dx), sum(dy) / len(dy)) / step_m * 100

    road_paths = [r["paths"] for r in (roads or []) if r.get("paths")][:6]
    front_m = (setbacks.get("front_ft") or 50) * FT + HALF_ROW_M
    side_m = (setbacks.get("side_ft") or 20) * FT
    hazards, missing = _hazard_polys(shape, state, in_spokane)
    cell_ac = step_m * step_m / 4046.86

    pts, counts = [], {"inside": 0, "setback": 0, "hazard": 0, "steep": 0}
    for j in range(ny):
        for i in range(nx):
            x, y = grid[j * nx + i]
            if not geo.point_in_polygon(x, y, rings):
                continue
            counts["inside"] += 1
            d_edge = geo.point_to_paths_m(x, y, rings)
            d_road = min((geo.point_to_paths_m(x, y, p) for p in road_paths), default=None)
            if d_edge < side_m or (d_road is not None and d_road < front_m):
                counts["setback"] += 1
                continue
            if any(geo.point_in_polygon(x, y, h) for h in hazards):
                counts["hazard"] += 1
                continue
            sp = slope_pct(i, j)
            if sp is not None and sp > MAX_SLOPE_PCT:
                counts["steep"] += 1
                continue
            pts.append((x, y, sp, d_road))
    if not counts["inside"]:
        return None
    buildable_ac = round(len(pts) * cell_ac, 2)
    out = {
        "buildable_acres": buildable_ac,
        "buildable_pct": round(len(pts) / counts["inside"] * 100),
        "parcel_acres": round(counts["inside"] * cell_ac, 2),
        "lost_to": {k: round(v * cell_ac, 2) for k, v in counts.items() if k != "inside" and v},
        **({"hazards_unchecked": missing} if missing else {}),
        "setbacks_ft": {"front": setbacks.get("front_ft") or 50, "side": setbacks.get("side_ft") or 20,
                        "source": setbacks.get("source") or "typical rural setbacks"},
    }
    if not pts:
        return out
    best = min(pts, key=lambda p: ((p[2] or 0) > GOOD_SLOPE_PCT, p[3] if p[3] is not None else 0, p[2] or 0))
    x, y, sp, d_road = best
    out["site"] = {
        "lat": round(y, 6), "lon": round(x, 6),
        "xy": [round((x - lon0) * kx), round((y - lat0) * ky)],   # meters from centroid, same frame as the card sketch
        "slope_pct": round(sp) if sp is not None else None,
        "driveway_ft": round(d_road / FT) if d_road is not None else None,
    }
    return out
