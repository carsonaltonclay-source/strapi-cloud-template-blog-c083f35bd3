"""'Can I actually build here?' checks from free public data.

* soil_septic   USDA NRCS soil survey rating "ENG - Septic Tank Absorption Fields"
* wildfire      USDA Forest Service Wildfire Hazard Potential (2023, 270 m)
* internet      measured fixed-internet speed tests near the parcel (WA only)
* power_company electric retail service territory (WA only)
* zoning_check  Spokane County minimum lot size per dwelling for the parcel's zone
"""

import json
import math

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
# Spokane County Code 14.612.220 / Table 612-1: single-family homes are permitted in the commercial
# zones; industrial and mineral lands allow only a caretaker's residence.
NO_HOMES_ZONES = ("Industrial", "Mineral Lands")


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
        "SELECT m.mukey, mu.muname, c.compname, c.comppct_r, c.drainagecl, c.hydricrating, ci.ruledepth, ci.interphrc, ci.interphr "
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
    weight, reasons, soils, hard_by_comp, drainage, hydric = {}, {}, [], {}, {}, 0.0
    for r in rows:
        pct = float(r.get("comppct_r") or 0)
        if str(r.get("ruledepth")) == "0":
            cls = r.get("interphrc") or "Not rated"
            weight[cls] = weight.get(cls, 0) + pct
            dcl = r.get("drainagecl")
            if dcl:
                drainage[dcl] = drainage.get(dcl, 0) + pct
            if r.get("hydricrating") == "Yes":
                hydric += pct
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
        "drainage": max(drainage, key=drainage.get) if drainage else None,
        "hydric_pct": round(hydric / total * 100) if total else 0,
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
    return {"label": label, "class": cls, "down_mbps": round(best) if best >= 1 else round(best, 1), "typical_mbps": round(typical), "tests": tests,
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
    return _lot_verdict(z, need, acres, "Spokane County")


def _need_txt(need):
    return f"{need:g} acres" if need >= 1 else f"{round(need * 43560, -2):,.0f} sq ft"


def _lot_verdict(z, need, acres, county):
    need_txt = _need_txt(need)
    if acres is None:
        return {"status": "unknown", "zone": z, "min_acres": need, "label": f"{z}: {need_txt} per home",
                "detail": f"{z} requires {need_txt} per home; lot size unknown.", "county": county}
    if acres + 0.05 < need:
        return {"status": "undersized", "zone": z, "min_acres": need,
                "label": f"Smaller than {z} minimum ({need_txt})",
                "detail": f"{acres:g} ac is below the {need_txt} per home that {z} requires for new lots. "
                          "Older lots of record are often still buildable — confirm with the county before buying.",
                "county": county}
    splits = int(acres // need) if need else 0
    if splits >= 2:
        return {"status": "splittable", "zone": z, "min_acres": need,
                "label": f"Meets {z} minimum; room for up to {splits} lots",
                "detail": f"{z} allows one home per {need_txt}. At {acres:g} ac this parcel could in principle be divided into "
                          f"{splits} lots (short plat rules, access and water still apply).", "county": county}
    return {"status": "ok", "zone": z, "min_acres": need, "label": f"Meets {z} minimum ({need_txt})",
            "detail": f"{acres:g} ac meets the {need_txt} per home that {z} requires.", "county": county}


# ---- zoning outside Spokane County ------------------------------------------------

# WA Dept. of Commerce Washington Zoning Atlas: general category -> verdict when there is no lot minimum.
WAZA_NO_HOMES = {"IND": "industrial", "PUB": "public facilities"}
WAZA_URBAN = {"LIR", "MR", "MXU"}

# Bonner County Revised Code 12-411, Table 4-1 (minimum lot size, acres).
BONNER_MIN_ACRES = {"F": 40, "A/f-20": 20, "A/f-10": 10, "R-10": 10, "R-5": 5}
BONNER_NO_HOMES = ("Commercial", "Industrial")


def zoning_elsewhere(lat, lon, county, state, acres):
    """Zoning verdict for WA counties other than Spokane (WA Zoning Atlas) and Bonner County ID."""
    if lat is None:
        return None
    try:
        if state == "WA":
            feats = arcgis.query(LAYERS["wa_zoning_atlas"], geometry=(lon, lat),
                                 out_fields="ZoneID,ZoneName,WAZAZoneGeneral,DenMinLotSizeSqFt,Jurisdiction")
            if not feats:
                return None
            a = feats[0]["attributes"]
            z = f"{a.get('ZoneName') or a.get('ZoneID')} ({a.get('ZoneID')})" if a.get("ZoneID") else (a.get("ZoneName") or "")
            where = a.get("Jurisdiction") or f"{county} County"
            gen, sqft = a.get("WAZAZoneGeneral"), a.get("DenMinLotSizeSqFt")
            if gen in WAZA_NO_HOMES:
                return _no_homes(z, where)
            if gen == "COM":
                return {"status": "unknown", "zone": z, "min_acres": None, "label": f"{z}: commercial zone",
                        "detail": f"Zoned {z} in {where}; some commercial zones allow a house, some don't — ask the planning office.",
                        "county": where}
            if gen == "TRB":
                return {"status": "unknown", "zone": z, "min_acres": None, "label": "Tribal lands",
                        "detail": "Tribal lands: county zoning doesn't apply; ask the tribe's planning office.", "county": where}
            if gen in WAZA_URBAN or (sqft and 0 < sqft < 43560 / 2):
                return {"status": "urban", "zone": z, "min_acres": None, "label": f"{z}: town lot sizes",
                        "detail": f"Zoned {z} in {where}; small lots, usually needing town water and sewer.", "county": where}
            if sqft and sqft > 0:
                v = _lot_verdict(z, sqft / 43560, acres, where)
                v["detail"] += " (WA Zoning Atlas)"
                return v
            return {"status": "unknown", "zone": z, "min_acres": None, "label": f"{z} zone",
                    "detail": f"Zoned {z} in {where}; no minimum lot size on file — ask the county.", "county": where}
        if county == "Bonner":
            feats = arcgis.query(LAYERS["bonner_zoning"], geometry=(lon, lat), out_fields="zonedesc")
            if not feats:
                return None
            z = feats[0]["attributes"].get("zonedesc") or ""
            code = z[z.rfind("(") + 1:z.rfind(")")] if "(" in z else ""
            if any(k in z for k in BONNER_NO_HOMES):
                return _no_homes(z, "Bonner County")
            if code in BONNER_MIN_ACRES:
                return _lot_verdict(z, BONNER_MIN_ACRES[code], acres, "Bonner County")
            if z.startswith("Suburban"):
                return {"status": "urban", "zone": z, "min_acres": 2.5, "label": f"{z}: 2.5 ac without town water/sewer",
                        "detail": "Bonner County Suburban zone: 2.5 acres per lot without public water and sewer, smaller with them.",
                        "county": "Bonner County"}
            return {"status": "unknown", "zone": z, "min_acres": None, "label": f"{z} zone",
                    "detail": f"Zoned {z}; ask Bonner County Planning about lot size.", "county": "Bonner County"}
    except HttpError:
        return None
    return None


def _no_homes(z, where):
    return {"status": "not_residential", "zone": z, "min_acres": None, "label": f"{z} zone: homes generally not allowed",
            "detail": f"Zoned {z} in {where}; a new house is generally not a permitted use. Check with the planning office.",
            "county": where}


# ---- power company and internet outside Washington's state layers -------------------

def power_company_national(lat, lon):
    """DOE/ORNL electric retail service territories (both states)."""
    if lat is None:
        return None
    try:
        feats = arcgis.query(LAYERS["us_electric_territories"], geometry=(lon, lat), out_fields="NAME,TYPE,TELEPHONE,WEBSITE")
    except HttpError:
        return None
    named = [f["attributes"] for f in feats if (f["attributes"].get("TYPE") or "").upper() != "FEDERAL" and f["attributes"].get("NAME")]
    # Overlapping territories: prefer the co-op / PUD (the local line owner) over the big investor-owned utility.
    named.sort(key=lambda a: (a.get("TYPE") or "").upper() == "INVESTOR OWNED")
    if not named:
        return None
    a = named[0]
    return {"name": a["NAME"].title().replace(", Inc", " Inc").replace("&", "and"), "phone": a.get("TELEPHONE") or "",
            "website": a.get("WEBSITE") or "", "source": "DOE electric service territories",
            **({"also": [x["NAME"].title() for x in named[1:3]]} if len(named) > 1 else {})}


def internet_ookla(lat, lon):
    """Measured home internet (Ookla open data, Esri Living Atlas), ~1 mile box, 2024 onward."""
    if lat is None:
        return None
    d_lat, d_lon = 0.0145, 0.0145 / max(0.2, math.cos(math.radians(lat)))
    try:
        feats = arcgis.query(LAYERS["ookla_fixed_tiles"],
                             geometry={"xmin": lon - d_lon, "ymin": lat - d_lat, "xmax": lon + d_lon, "ymax": lat + d_lat,
                                       "spatialReference": {"wkid": 4326}},
                             where="CalYear >= 2024", out_fields="AvgDown,Tests")
    except HttpError:
        return None
    tiles = [f["attributes"] for f in feats if (f["attributes"].get("AvgDown") or 0) > 0]
    src = "Ookla open data (Esri Living Atlas)"
    if not tiles:
        return {"label": "No speed tests nearby", "class": "unknown", "down_mbps": None, "tests": 0, "source": src}
    downs = sorted(t["AvgDown"] for t in tiles)
    best, typical = downs[int(len(downs) * 0.9)] if len(downs) >= 10 else downs[-1], downs[len(downs) // 2]
    cls = "fast" if best >= 100 else "ok" if best >= 25 else "slow"
    return {"label": {"fast": "Fast internet nearby", "ok": "Moderate internet nearby", "slow": "Slow internet nearby"}[cls],
            "class": cls, "down_mbps": round(best) if best >= 1 else round(best, 1), "typical_mbps": round(typical),
            "tests": sum(int(t.get("Tests") or 0) for t in tiles), "source": src}


def cell_service(lat, lon):
    """Measured mobile data speeds (Ookla open data) within about a mile, 2024 onward."""
    if lat is None:
        return None
    d_lat, d_lon = 0.0145, 0.0145 / max(0.2, math.cos(math.radians(lat)))
    try:
        feats = arcgis.query(LAYERS["ookla_mobile_tiles"],
                             geometry={"xmin": lon - d_lon, "ymin": lat - d_lat, "xmax": lon + d_lon, "ymax": lat + d_lat,
                                       "spatialReference": {"wkid": 4326}},
                             where="CalYear >= 2024", out_fields="AvgDown,Tests")
    except HttpError:
        return None
    tiles = [f["attributes"] for f in feats if (f["attributes"].get("AvgDown") or 0) > 0]
    src = "Ookla mobile speed tests (Esri Living Atlas)"
    tests = sum(int(t.get("Tests") or 0) for t in tiles)
    if not tiles:
        return {"class": "none", "label": "No mobile speed tests within a mile", "tests": 0, "source": src,
                "detail": "No phone has run a speed test nearby since 2024 — coverage may be weak; check each carrier's map."}
    downs = sorted(t["AvgDown"] for t in tiles)
    typical = downs[len(downs) // 2]
    cls = "good" if typical >= 25 and len(tiles) >= 3 else "ok" if typical >= 5 else "poor"
    return {"class": cls, "label": {"good": "Good cell data nearby", "ok": "Some cell data nearby", "poor": "Weak cell data nearby"}[cls],
            "typical_mbps": round(typical), "tiles": len(tiles), "tests": tests, "source": src,
            "detail": f"{tests} phone speed tests within ~1 mile since 2024, typical {round(typical)} Mbps download."}
