"""Hidden deal-breakers: things that stop a purchase late in the process.

Each check returns {key, level, label, detail, source[, ask]} where level is
"bad" (can stop a purchase), "warn" (read the fine print / ask) or "ok"
(checked, nothing found). Checks whose data doesn't cover the parcel are
left out rather than reported as "ok".

* wetlands       USFWS National Wetlands Inventory (WA + ID), share of the parcel
* landslides     WA DNR landslide inventory (WA)
* streams        Spokane County DNR stream buffers (riparian setbacks)
* mines          WA DNR active surface-mine permits; USGS topo-map gravel pits (ID)
* nitrate        well nitrate tests nearby: WA DNR groundwater chemistry, Spokane
                 County well tests, Idaho DEQ nitrate monitoring wells
* listing text   HOA, covenants, no manufactured homes, "not buildable" ... (remarks.red_flags)
"""

import statistics

from . import arcgis, geo, remarks
from .config import LAYERS
from .http import HttpError

FT = 0.3048
MILE_M = 1609.34


def _grid(rings, n=12):
    """Up to n*n sample points inside the parcel."""
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pts = []
    for i in range(n):
        for j in range(n):
            x = x0 + (i + 0.5) * (x1 - x0) / n
            y = y0 + (j + 0.5) * (y1 - y0) / n
            if geo.point_in_polygon(x, y, rings):
                pts.append((x, y))
    return pts


def _share(rings, feats):
    """Fraction of the parcel covered by any of the polygons in feats."""
    pts = _grid(rings)
    polys = [f["geometry"]["rings"] for f in feats if (f.get("geometry") or {}).get("rings")]
    if not pts or not polys:
        return 0.0
    inside = sum(1 for x, y in pts if any(geo.point_in_polygon(x, y, p) for p in polys))
    return inside / len(pts)


def _target(shape, lat, lon):
    return shape if shape else (lon, lat)


# Hazard layers shared with homesite.py: identical queries, so the second caller gets the HTTP cache.
HAZARD_QUERIES = {
    "nwi_wetlands": ("1=1", "ATTRIBUTE,WETLAND_TYPE,ACRES"),
    "fema_flood": ("SFHA_TF = 'T'", "FLD_ZONE,ZONE_SUBTY"),
    "wa_landslides": ("1=1", "LANDSLIDE_TYPE,LANDSLIDE_CERTAINTY"),
    "sc_stream_buffers": ("1=1", "DESCRIPTIO,BUFF_DIST"),
}


def hazard_query(layer, shape, lat, lon):
    where, fields = HAZARD_QUERIES[layer]
    return arcgis.query(LAYERS[layer], geometry=_target(shape, lat, lon), distance_m=None if shape else 60,
                        where=where, out_fields=fields, return_geometry=bool(shape))


def wetlands(shape, lat, lon):
    try:
        feats = hazard_query("nwi_wetlands", shape, lat, lon)
    except HttpError:
        return None
    src = "USFWS National Wetlands Inventory"
    if not feats:
        return {"key": "wetlands", "level": "ok", "label": "No mapped wetlands", "source": src}
    types = sorted({f["attributes"].get("WETLAND_TYPE") or "Wetland" for f in feats})
    share = _share(shape["rings"], feats) if shape else None
    only_streams = all(t.startswith("Riverine") for t in types)
    what = ", ".join(types[:3]).lower()
    if share is not None and share >= 0.5 and not only_streams:
        return {"key": "wetlands", "level": "bad", "label": f"About {round(share * 100)}% of the parcel is mapped wetland",
                "detail": f"{what}; building in wetlands and their buffers (25–250 ft by wetland category) is heavily restricted", "source": src,
                "ask": "Has a wetland delineation been done? Where is the buildable area outside the wetland buffer?"}
    pct = f"about {round(share * 100)}% of the parcel" if share else "touches the parcel"
    if share is not None and share < 0.03:
        return {"key": "wetlands", "level": "ok", "label": "Only a sliver of mapped wetland/stream at the edge",
                "detail": f"{what}, {pct}", "source": src, "streams_only": only_streams}
    return {"key": "wetlands", "level": "warn", "streams_only": only_streams, "label": "Mapped wetland or stream on the parcel" if not only_streams else "Stream on the parcel",
            "detail": f"{what} — {pct}; expect buffers (25–250 ft depending on the wetland's category) where building is restricted", "source": src,
            "ask": "Where would the house, well and septic go given the wetland or stream buffer?"}


def flood(shape, lat, lon, soil):
    """FEMA flood zones (both states) plus poorly drained / hydric soils (drainage problems)."""
    src = "FEMA flood maps"
    try:
        feats = hazard_query("fema_flood", shape, lat, lon)
    except HttpError:
        feats = None
    poor = soil and (soil.get("drainage") in ("Poorly drained", "Very poorly drained") or (soil.get("hydric_pct") or 0) >= 50)
    if feats:
        zones = sorted({f["attributes"].get("FLD_ZONE") or "A" for f in feats})
        share = _share(shape["rings"], feats) if shape else None
        big = share is not None and share >= 0.5
        return {"key": "flood", "level": "bad" if big else "warn",
                "label": f"{'Mostly in' if big else 'Partly in'} a FEMA flood zone ({', '.join(zones)})",
                "detail": (f"about {round(share * 100)}% of the parcel; " if share else "") +
                          "building there needs an elevated floor and flood insurance is required with a mortgage",
                "source": src, "ask": "Where is the high ground for the house? Has an elevation certificate been done?"}
    if poor:
        hyd = soil.get("hydric_pct") or 0
        what = (soil.get("drainage") or "hydric").lower() + (f", {hyd}% hydric" if hyd else "")
        return {"key": "flood", "level": "warn", "label": "Wet, poorly drained soils",
                "detail": f"USDA soils: {what}; expect standing water in spring, drainage work and a raised septic",
                "source": "USDA soil survey"}
    if feats is None:
        return None
    dr = (soil or {}).get("drainage")
    # No hazard polygons can mean "minimal hazard" (zone X) or "never mapped": ask which.
    try:
        zones = {f["attributes"].get("FLD_ZONE") for f in arcgis.query(
            LAYERS["fema_flood"], geometry=_target(shape, lat, lon), distance_m=None if shape else 60, out_fields="FLD_ZONE")}
    except HttpError:
        zones = None
    if zones is not None and not (zones - {"D", "AREA NOT INCLUDED", None}):
        return {"key": "flood", "level": "info",
                "label": "Flood risk not studied (FEMA zone D)" if "D" in zones else "No FEMA flood map for this area",
                "detail": "no official flood zones here — look for creeks, ponds and low ground, and ask the county"
                          + (f"; soils {dr.lower()}" if dr else ""), "source": src}
    return {"key": "flood", "level": "ok", "label": "Not in a FEMA flood zone",
            "detail": f"soils {dr.lower()}" if dr else "", "source": src}


def landslides(shape, lat, lon, state):
    if state != "WA":
        return None
    try:
        feats = hazard_query("wa_landslides", shape, lat, lon)
    except HttpError:
        return None
    src = "WA DNR landslide inventory"
    if not feats:
        return {"key": "landslides", "level": "ok", "label": "No mapped landslides", "source": src}
    share = _share(shape["rings"], feats) if shape else None
    kind = (feats[0]["attributes"].get("LANDSLIDE_TYPE") or "landslide").lower()
    big = share is not None and share >= 0.5
    return {"key": "landslides", "level": "bad" if big else "warn",
            "label": "Mapped landslide on the parcel" if big else "Mapped landslide touches the parcel",
            "detail": f"{kind}{f', about {round(share * 100)}% of the parcel' if share else ''}; "
                      "a geotechnical report is usually required before a permit", "source": src,
            "ask": "Has a geotechnical (landslide) report been done for a building site?"}


def streams(shape, lat, lon, in_spokane):
    if not in_spokane:
        return None
    try:
        feats = hazard_query("sc_stream_buffers", shape, lat, lon)
    except HttpError:
        return None
    src = "Spokane County critical areas"
    if not feats:
        return {"key": "streams", "level": "ok", "label": "No stream buffers", "source": src}
    a = feats[0]["attributes"]
    share = _share(shape["rings"], feats) if shape else None
    big = share is not None and share >= 0.6
    if share is not None and share < 0.05:
        return {"key": "streams", "level": "ok", "label": "Stream buffer only clips the edge",
                "detail": f"{(a.get('DESCRIPTIO') or 'stream').lower()} buffer, about {round(share * 100)}% of the parcel", "source": src}
    return {"key": "streams", "level": "bad" if big else "warn",
            "label": f"{'Most of the parcel is in' if big else 'Parcel is partly in'} a stream buffer",
            "detail": f"{(a.get('DESCRIPTIO') or 'stream').lower()} buffer of {a.get('BUFF_DIST') or '?'} ft"
                      f"{f', about {round(share * 100)}% of the parcel' if share else ''}; no building inside the buffer",
            "source": src}


def mines(lat, lon, state):
    if lat is None:
        return None
    src_wa, src_id = "WA DNR active surface-mine permits", "USGS topographic-map mine features"
    try:
        if state == "WA":
            feats = arcgis.query(LAYERS["wa_mines"], geometry=(lon, lat), distance_m=MILE_M,
                                 out_fields="MINE_NAME,COMMODITY_DESC,PERMIT_ACREAGE,LATITUDE,LONGITUDE")
            sites = [(geo.miles_between(lat, lon, f["attributes"]["LATITUDE"], f["attributes"]["LONGITUDE"]),
                      f["attributes"].get("MINE_NAME") or "mine", (f["attributes"].get("COMMODITY_DESC") or "").lower(),
                      f["attributes"].get("PERMIT_ACREAGE"))
                     for f in feats if f["attributes"].get("LATITUDE")]
            src = src_wa
        else:
            feats = arcgis.query(LAYERS["usgs_mine_points"], geometry=(lon, lat), distance_m=MILE_M / 2,
                                 where="ftr_type LIKE '%Pit%' OR ftr_type LIKE '%Quarry%'",
                                 out_fields="ftr_type,ftr_name,topo_date", return_geometry=True)
            sites = [(geo.miles_between(lat, lon, f["geometry"]["y"], f["geometry"]["x"]),
                      f["attributes"].get("ftr_name") or f["attributes"].get("ftr_type") or "pit",
                      (f["attributes"].get("ftr_type") or "").lower(), None)
                     for f in feats if f.get("geometry")]
            src = src_id
    except HttpError:
        return None
    if not sites:
        return {"key": "mines", "level": "ok",
                "label": "No active mines within a mile" if state == "WA" else "No mapped pits within half a mile",
                "source": src}
    d, name, what, acres = min(sites)
    if state == "WA":
        return {"key": "mines", "level": "warn", "label": f"Active mine {d:.1f} mi away",
                "detail": f"{name.title()}{f' ({what})' if what else ''}{f', {acres:g}-acre permit' if acres else ''}: "
                          "expect truck traffic, noise and dust", "source": src}
    return {"key": "mines", "level": "warn", "label": f"Gravel pit or quarry mapped {d:.1f} mi away",
            "detail": f"{name} ({what}); from older USGS topo maps — may no longer be active", "source": src}


def _num(v):
    try:
        return float(str(v).replace("<", "").strip())
    except (TypeError, ValueError):
        return None


def nitrate(lat, lon, state, in_spokane):
    if lat is None:
        return None
    vals, src = [], []
    queries = []
    if state == "WA":
        queries.append(("wa_groundwater_chem", "NITRATE_MGL_AS_N", "WA DNR groundwater chemistry"))
        if in_spokane:
            queries += [("sc_nitrate_55", "nitrate", "Spokane County well tests"),
                        ("sc_nitrate_56", "nitrate_re", "Spokane County well tests"),
                        ("sc_nitrate_57", "nitrate_re", "Spokane County well tests")]
    else:
        queries.append(("id_nitrate_wells", "N03_MGL", "Idaho DEQ nitrate monitoring"))
    for layer, field, label in queries:
        try:
            feats = arcgis.query(LAYERS[layer], geometry=(lon, lat), distance_m=MILE_M * 1.5, out_fields=field)
        except HttpError:
            continue
        got = [v for v in (_num(f["attributes"].get(field)) for f in feats) if v is not None and v >= 0]
        if got:
            vals += got
            if label not in src:
                src.append(label)
    if len(vals) < 3:
        return None
    med, high = statistics.median(vals), max(vals)
    over = sum(1 for v in vals if v >= 10)
    source = ", ".join(src)
    detail = f"{len(vals)} well tests within 1.5 mi: median {med:.1f} mg/L, highest {high:.1f} (limit 10)"
    if med >= 10:
        return {"key": "nitrate", "level": "bad", "label": "High nitrate in nearby wells", "detail": detail, "source": source,
                "ask": "Is there a recent water test for the well (nitrate, bacteria)? Budget for treatment."}
    if over or med >= 5:
        return {"key": "nitrate", "level": "warn", "label": "Elevated nitrate in some nearby wells",
                "detail": f"{detail}; {over} over the drinking-water limit" if over else detail, "source": source,
                "ask": "Is there a recent nitrate test for the well or neighbors' wells?"}
    return {"key": "nitrate", "level": "ok", "label": "Nitrate normal in nearby wells", "detail": detail, "source": source}


def school_district(lat, lon):
    if lat is None:
        return None
    try:
        feats = arcgis.query(LAYERS["tiger_school_unified"], geometry=(lon, lat), out_fields="NAME")
    except HttpError:
        return None
    return feats[0]["attributes"]["NAME"] if feats else None


def run_all(shape, lat, lon, state, in_spokane, remarks_text, soil=None):
    checks = []
    for fn, args in ((flood, (shape, lat, lon, soil)), (wetlands, (shape, lat, lon)), (landslides, (shape, lat, lon, state)),
                     (streams, (shape, lat, lon, in_spokane)), (mines, (lat, lon, state)),
                     (nitrate, (lat, lon, state, in_spokane))):
        try:
            c = fn(*args)
        except (HttpError, KeyError, TypeError, ValueError):
            c = None
        if c:
            checks.append(c)
    # A stream already covered by the county's stream-buffer check isn't a second warning.
    if any(c["key"] == "streams" for c in checks):
        for c in checks:
            if c["key"] == "wetlands" and c.get("streams_only") and c["level"] == "warn":
                c.update(level="ok", label="Stream mapped on the parcel (see stream buffer)")
    for c in checks:
        c.pop("streams_only", None)
    checks += remarks.red_flags(remarks_text)
    return checks
