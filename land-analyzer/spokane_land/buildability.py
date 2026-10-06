"""'Can I actually build here?' checks from free public data.

* soil_septic   USDA NRCS soil survey rating "ENG - Septic Tank Absorption Fields"
* wildfire      USDA Forest Service Wildfire Hazard Potential (2023, 270 m)
* internet      measured fixed-internet speed tests near the parcel (WA only)
* power_company electric retail service territory (WA only)
* zoning_check  Spokane County minimum lot size per dwelling for the parcel's zone
"""

import json

from . import arcgis, geo
from .config import LAYERS, SDA_URL, WILDFIRE_IMAGE_SERVER
from .http import HttpError, get_json

SEPTIC_RULE = "ENG - Septic Tank Absorption Fields"
SOIL_RANK = {"Not limited": 1, "Somewhat limited": 2, "Very limited": 3, "Not rated": 0}
# USDA limitations that usually mean a mound/engineered system or no drain field at all.
# The rest (seepage / filtering capacity of gravelly soils, stones, slope) are very common
# around Spokane and are usually handled with pressure distribution or extra treatment.
HARD_SOIL_LIMITS = ("Depth to bedrock", "Depth to saturated zone", "Slow water movement", "Flooding", "Ponding",
                    "Depth to cemented pan", "Subsidence")

WHP_CLASSES = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high",
               6: "Non-burnable", 7: "Water"}

# Spokane County Zoning Code, tables 616-3 and 618-3: minimum lot area per dwelling unit (acres).
ZONE_MIN_ACRES = {
    "Rural-5": 5, "Rural Traditional": 10, "Rural Conservation": 20, "Urban Reserve": 20,
    "Rural Activity Center": 10000 / 43560,
    "Large Tract Agricultural": 40, "Small Tract Agricultural": 10, "Forest Land": 20,
}
NO_HOMES_ZONES = ("Industrial", "Mineral Lands", "Commercial")


def _wkt(rings, max_pts=80):
    ring = max(rings, key=len)
    step = max(1, len(ring) // max_pts)
    pts = ring[::step]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return None
    return "POLYGON((" + ", ".join(f"{x:.6f} {y:.6f}" for x, y in pts) + "))"


def soil_septic(geometry, lat, lon):
    """Septic suitability of the parcel's soils (area-weighted over map units)."""
    wkt = _wkt(geometry["rings"]) if geometry and geometry.get("rings") else None
    if not wkt:
        if lat is None:
            return None
        wkt = f"POINT({lon:.6f} {lat:.6f})"
    sql = (
        "SELECT m.mukey, mu.muname, c.compname, c.comppct_r, ci.ruledepth, ci.interphrc, ci.interphr "
        f"FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}') AS m "
        "JOIN mapunit mu ON mu.mukey = m.mukey JOIN component c ON c.mukey = m.mukey "
        "JOIN cointerp ci ON ci.cokey = c.cokey "
        f"WHERE ci.mrulename = '{SEPTIC_RULE}' AND c.majcompflag = 'Yes' "
        "AND (ci.ruledepth = 0 OR ci.interphr > 0) ORDER BY c.comppct_r DESC, ci.ruledepth"
    )
    try:
        data = get_json(SDA_URL, {"query": sql, "format": "JSON+COLUMNNAME"}, post=True)
    except HttpError:
        return None
    table = data.get("Table") or []
    if len(table) < 2:
        return None
    cols, rows = table[0], [dict(zip(table[0], r)) for r in table[1:]]
    weight, reasons, soils, hard_by_comp = {}, {}, [], {}
    for r in rows:
        pct = float(r.get("comppct_r") or 0)
        if str(r.get("ruledepth")) == "0":
            cls = r.get("interphrc") or "Not rated"
            weight[cls] = weight.get(cls, 0) + pct
            name = r.get("muname") or ""
            if name and name not in soils:
                soils.append(name)
        elif r.get("interphrc") and float(r.get("interphr") or 0) >= 0.5:
            reasons[r["interphrc"]] = reasons.get(r["interphrc"], 0) + pct
            # a component counts as hard only when a hard limit is rated at full severity
            if r["interphrc"] in HARD_SOIL_LIMITS and float(r.get("interphr") or 0) >= 0.99:
                key = (r.get("mukey"), r.get("compname"), pct)
                hard_by_comp[key] = pct
    if not weight:
        return None
    total = sum(weight.values())
    dominant = max(weight, key=lambda k: (weight[k], SOIL_RANK.get(k, 0)))
    worst = max(weight, key=lambda k: SOIL_RANK.get(k, 0))
    hard = {k: v for k, v in reasons.items() if k in HARD_SOIL_LIMITS}
    hard_share = sum(hard_by_comp.values()) / total
    if dominant == "Not rated":
        outlook = "unknown"
    elif hard_share >= 0.5:
        outlook = "hard"
    elif dominant in ("Not limited", "Somewhat limited"):
        outlook = "good"
    else:
        outlook = "design"
    return {
        "rating": dominant,
        "worst": worst,
        "outlook": outlook,
        "hard_limits": [k for k, _ in sorted(hard.items(), key=lambda kv: -kv[1])][:2],
        "share": {k: round(v / total * 100) for k, v in weight.items()},
        "reasons": [k for k, _ in sorted(reasons.items(), key=lambda kv: -kv[1])][:3],
        "soils": soils[:3],
        "source": "USDA NRCS soil survey",
    }


def wildfire(geometry, lat, lon):
    """Highest wildfire hazard class found at a few points on the parcel."""
    pts = []
    if geometry and geometry.get("rings"):
        ring = max(geometry["rings"], key=len)
        clat, clon = geo.polygon_centroid(geometry["rings"])
        pts = [(clon, clat)] + [tuple(p) for p in ring[:: max(1, len(ring) // 6)]][:6]
    elif lat is not None:
        pts = [(lon, lat)]
    if not pts:
        return None
    try:
        data = get_json(WILDFIRE_IMAGE_SERVER + "/getSamples", {
            "geometry": json.dumps({"points": [[round(x, 6), round(y, 6)] for x, y in pts], "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true", "f": "json",
        }, post=True)
    except HttpError:
        return None
    vals = []
    for s in data.get("samples", []):
        try:
            vals.append(int(float(s["value"])))
        except (KeyError, TypeError, ValueError):
            pass
    burnable = [v for v in vals if 1 <= v <= 5]
    if not burnable:
        if vals:
            return {"class": max(vals), "label": WHP_CLASSES.get(max(vals), "Unknown"), "source": "USFS Wildfire Hazard Potential 2023"}
        return None
    top = max(burnable)
    return {"class": top, "label": WHP_CLASSES[top], "source": "USFS Wildfire Hazard Potential 2023"}


def internet(lat, lon, state):
    """Typical measured fixed-internet speed within ~1 mile (Washington only)."""
    if state != "WA" or lat is None:
        return None
    try:
        feats = arcgis.query(LAYERS["wa_speed_tiles"], geometry=(lon, lat), distance_m=1600,
                             where="CalYear >= 2022", out_fields="AvgDown,AvgUp,Tests,Service_Level")
    except HttpError:
        return None
    tiles = [f["attributes"] for f in feats if (f["attributes"].get("AvgDown") or 0) > 0]
    if not tiles:
        return {"label": "No speed tests nearby", "class": "unknown", "down_mbps": None, "tests": 0,
                "source": "Ookla speed tests via WA State Broadband Office"}
    downs = sorted(t["AvgDown"] for t in tiles)
    best = downs[-1]
    typical = downs[len(downs) // 2]
    tests = sum(int(t.get("Tests") or 0) for t in tiles)
    cls = "fast" if best >= 100 else "ok" if best >= 25 else "slow"
    label = {"fast": "Fast internet nearby", "ok": "Moderate internet nearby", "slow": "Slow internet nearby"}[cls]
    return {"label": label, "class": cls, "down_mbps": round(best), "typical_mbps": round(typical), "tests": tests,
            "source": "Ookla speed tests via WA State Broadband Office"}


def power_company(lat, lon, state):
    if state != "WA" or lat is None:
        return None
    try:
        feats = arcgis.query(LAYERS["wa_electric_territories"], geometry=(lon, lat),
                             out_fields="NAME,TYPE,TELEPHONE,WEBSITE")
    except HttpError:
        return None
    for f in feats:
        a = f["attributes"]
        if (a.get("TYPE") or "").upper() != "FEDERAL" and a.get("NAME"):
            return {"name": a["NAME"].title().replace("&", "and").replace(" And ", " & "),
                    "phone": a.get("TELEPHONE") or "", "website": a.get("WEBSITE") or "",
                    "source": "WA electric retail service territories"}
    return None


def zoning_check(zoning, acres):
    """Verdict on lot size vs the zone's minimum lot area per dwelling (Spokane County)."""
    if not zoning:
        return None
    zones = [z.strip() for z in zoning.split(",") if z.strip()]
    for z in zones:
        if any(k in z for k in NO_HOMES_ZONES):
            return {"status": "not_residential", "zone": z, "min_acres": None,
                    "label": f"{z} zone: homes generally not allowed",
                    "detail": f"Zoned {z}; new houses are generally not a permitted use. Check with Spokane County Building & Planning."}
    known = [(z, ZONE_MIN_ACRES[z]) for z in zones if z in ZONE_MIN_ACRES]
    if not known:
        z = zones[0]
        urban = "Residential" in z or "Mixed Use" in z
        return {"status": "urban" if urban else "unknown", "zone": z, "min_acres": None,
                "label": f"{z} zone" + (": urban lot sizes" if urban else ""),
                "detail": f"Zoned {z}." + (" Urban zones allow small lots but need public water and sewer." if urban else "")}
    z, need = max(known, key=lambda kv: kv[1])
    need_txt = f"{need:g} acres" if need >= 1 else "10,000 sq ft"
    if acres is None:
        return {"status": "unknown", "zone": z, "min_acres": need, "label": f"{z}: {need_txt} per home",
                "detail": f"{z} requires {need_txt} per home; lot size unknown."}
    if acres + 0.05 < need:
        return {"status": "undersized", "zone": z, "min_acres": need,
                "label": f"Smaller than {z} minimum ({need_txt})",
                "detail": f"{acres:g} ac is below the {need_txt} per home that {z} requires for new lots. "
                          "Older lots of record are often still buildable — confirm with the county before buying."}
    splits = int(acres // need) if need else 0
    if splits >= 2:
        return {"status": "splittable", "zone": z, "min_acres": need,
                "label": f"Meets {z} minimum; room for up to {splits} lots",
                "detail": f"{z} allows one home per {need_txt}. At {acres:g} ac this parcel could in principle be divided into "
                          f"{splits} lots (short plat rules, access and water still apply)."}
    return {"status": "ok", "zone": z, "min_acres": need, "label": f"Meets {z} minimum ({need_txt})",
            "detail": f"{acres:g} ac meets the {need_txt} per home that {z} requires."}
