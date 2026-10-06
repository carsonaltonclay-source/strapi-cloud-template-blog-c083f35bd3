"""Turn listing text + GIS facts into a verdict per category and a score."""

import math

from . import geo, remarks
from .config import ROAD_FRONTAGE_TOLERANCE_M, SPOKANE_LAT, SPOKANE_LON
from .models import HIGH, LOW, MEDIUM, Evidence, Finding, conf_rank

# status -> (label, points out of 25). Order = precedence when confidence ties.
CATEGORIES = {
    "water": {
        "shared_well": ("Shared well", 19),
        "well": ("Well on property", 25),
        "public": ("Public water available", 25),
        "none": ("No water — well needed", 6),
        "public_area": ("In public water service area (hookup unconfirmed)", 17),
        "district_wells": ("Water district area, neighbors on wells", 11),
        "needs_well": ("No well on record — well needed", 8),
        "well_possible": ("No well on record — wells nearby", 9),
    },
    "electric": {
        "on_site": ("Power on property", 25),
        "at_road": ("Power at road / nearby", 21),
        "none": ("No power (off-grid)", 2),
        "likely_near": ("Power likely close (neighbor structures)", 16),
        "possible": ("Power possibly within reach", 9),
        "far": ("Power likely far — costly extension", 3),
        "solar": ("Solar mentioned", 6),
    },
    "septic": {
        "failed": ("Failed perc / septic not feasible", 0),
        "sewer": ("Public sewer available", 25),
        "installed": ("Septic system installed", 25),
        "approved": ("Perc / septic design approved", 22),
        "needed": ("Septic required — perc needed", 8),
        "sewer_area": ("Inside city/UGA — sewer may be available", 15),
        "required": ("Septic required — perc not documented", 9),
        "mentioned": ("Septic mentioned — status unclear", 10),
    },
    "access": {
        "landlocked": ("Possibly landlocked", 0),
        "easement": ("Access by easement", 13),
        "private_road": ("Private road access", 16),
        "seasonal": ("Seasonal / rough access", 8),
        "public_road": ("Public road frontage", 25),
        "road_frontage": ("Road frontage (public/private unverified)", 19),
    },
}
UNKNOWN_POINTS = 8
HEALTH_DISTRICTS = {
    "Spokane": "Spokane Regional Health District",
    "Stevens": "Northeast Tri County Health District",
    "Pend Oreille": "Northeast Tri County Health District",
    "Lincoln": "Lincoln County Health Department",
    "Whitman": "Whitman County Public Health",
    "Kootenai": "Panhandle Health District",
    "Bonner": "Panhandle Health District",
    "Benewah": "Panhandle Health District",
}
FT = geo.METERS_PER_FOOT


def _pick(category, evidence):
    if not evidence:
        return Finding(evidence=[])
    order = list(CATEGORIES[category])
    best = max(evidence, key=lambda e: (conf_rank(e.confidence),
                                        -order.index(e.status) if e.status in order else -99))
    label = CATEGORIES[category].get(best.status, (best.status, UNKNOWN_POINTS))[0]
    # Strongest evidence first in the explanation.
    ev = sorted(evidence, key=lambda e: -conf_rank(e.confidence))
    return Finding(status=best.status, label=label, confidence=best.confidence, evidence=ev)


def _points(category, finding):
    if finding.status == "unknown":
        return UNKNOWN_POINTS
    return CATEGORIES[category].get(finding.status, ("", UNKNOWN_POINTS))[1]


def gis_evidence(listing, facts):
    ev = {"water": [], "electric": [], "septic": [], "access": []}
    flags = []
    parcel = facts.get("parcel")
    state = facts.get("state")
    acres = listing.lot_acres or (parcel.acres if parcel else None)

    # A listing much smaller than its county parcel is usually a new lot being
    # split off; county data then describes the whole parent parcel.
    split = _split_lot(listing, parcel)
    if split:
        flags.append(f"Listing is {listing.lot_acres:.2f} ac but sits on a {split:.1f}-ac county parcel — "
                     "likely a new lot split from it; parcel-level facts describe the whole parcel")

    # ---- water ---------------------------------------------------------------
    wells = facts.get("wells")
    src_w = "WA Ecology well reports" if state == "WA" else "Idaho IDWR well database"
    if wells and split:
        reach = math.sqrt(listing.lot_acres * 4046.86 / math.pi) * 1.6 + 30
        on_lot, parent = [], []
        for w in wells["on_parcel"]:
            if w.get("x") is None or listing.lat is None:
                parent.append(w)
                continue
            d = geo.haversine_m(listing.lat, listing.lon, w["y"], w["x"])
            (on_lot if d <= reach else parent).append(w)
        for w in parent[:1]:
            d = (geo.haversine_m(listing.lat, listing.lon, w["y"], w["x"]) / FT
                 if w.get("x") is not None and listing.lat is not None else None)
            ev["water"].append(Evidence(
                "well_possible", LOW,
                f"A well is recorded on the larger parent parcel"
                + (f", about {d:,.0f} ft from this lot's map pin" if d else "")
                + " — ask whether this lot has its own well or a share", src_w))
        wells = dict(wells, on_parcel=on_lot)
    if wells:
        for w in wells["on_parcel"][:3]:
            bits = [f"well {w['tag']}" if w.get("tag") else "water well"]
            if w.get("year"):
                bits.append(f"drilled {w['year']}")
            if w.get("depth"):
                bits.append(f"{w['depth']:.0f} ft deep")
            if w.get("gpm"):
                bits.append(f"{w['gpm']:g} gpm")
            ev["water"].append(Evidence("well", HIGH, "Well log on this parcel: " + ", ".join(bits), src_w))
        if wells["decommissioned_on_parcel"] and not wells["on_parcel"]:
            flags.append("A decommissioned water well is recorded on this parcel")
        stats = _well_stats(wells)
        districts = facts.get("water_district") or []
        if districts and not wells["on_parcel"]:
            if wells.get("within_quarter_mile", 0) >= 3:
                # Purveyor boundaries are broad; many private wells next door
                # usually means no main on this road.
                ev["water"].append(Evidence(
                    "district_wells", MEDIUM,
                    f"Inside {', '.join(districts)} boundary, but {wells['within_quarter_mile']} private "
                    "wells within 1/4 mile suggest no water main here — ask the purveyor",
                    "Public water service areas + well logs"))
            else:
                ev["water"].append(Evidence(
                    "public_area", MEDIUM,
                    f"Inside {', '.join(districts)} service area — confirm a main reaches the lot and get hookup costs",
                    "Public water service areas"))
        if not wells["on_parcel"] and not districts:
            if wells["nearby_count"]:
                ev["water"].append(Evidence(
                    "needs_well", MEDIUM,
                    "No well log for this parcel and outside public water areas. " + stats, src_w))
            else:
                ev["water"].append(Evidence(
                    "needs_well", LOW, "No well log for this parcel and no water wells within 1 mile", src_w))
                flags.append("No water wells on record within 1 mile — groundwater availability unproven")
        elif stats and not wells["on_parcel"]:
            ev["water"][-1].detail += f". Fallback well data: {stats}"

    # ---- electric (proxies: structures nearby) ---------------------------------
    if parcel and _improved(parcel.land_use):
        ev["electric"].append(Evidence("on_site", MEDIUM,
                                       f"Assessor land use is '{parcel.land_use}' (improved parcel)",
                                       "Spokane County Assessor"))
    nb = facts.get("neighbors")
    if nb:
        if nb["on_parcel"]:
            ev["electric"].append(Evidence("on_site", LOW,
                                           f"Addressed site on parcel: {nb['on_parcel'][0]}",
                                           "Spokane County address points"))
        n = nb["nearest_ft"]
        src = "Spokane County address points (proxy: addressed structures have power)"
        if n is None:
            ev["electric"].append(Evidence("far", LOW, "No addressed structures within 0.5 mile", src))
        elif n <= 600:
            ev["electric"].append(Evidence("likely_near", MEDIUM,
                                           f"Nearest addressed structure {n:,} ft from the lot ({nb['count']} within 0.5 mi)", src))
        elif n <= 1500:
            ev["electric"].append(Evidence("possible", LOW,
                                           f"Nearest addressed structure {n:,} ft away", src))
        else:
            ev["electric"].append(Evidence("far", LOW, f"Nearest addressed structure {n:,} ft away", src))
    elif wells and state:
        src = f"{src_w} (proxy: domestic wells mark homes with power)"
        n = wells.get("nearest_ft")
        if wells["on_parcel"]:
            ev["electric"].append(Evidence("likely_near", LOW, "Parcel has a water well (pump needs power)", src))
        elif n is not None and n <= 1000:
            ev["electric"].append(Evidence("likely_near", LOW, f"Nearest water well {n:,} ft away", src))
        elif n is not None and n <= 2640:
            ev["electric"].append(Evidence("possible", LOW, f"Nearest water well {n:,} ft away", src))
        else:
            ev["electric"].append(Evidence("far", LOW, "No water wells (homes) within 0.5 mile", src))

    # ---- septic / sewer ----------------------------------------------------------
    city, uga = facts.get("city") or [], facts.get("uga") or []
    if state == "WA" and "city" in facts:
        if city or uga:
            where = f"city of {city[0]}" if city else f"{uga[0]} urban growth area"
            ev["septic"].append(Evidence("sewer_area", MEDIUM,
                                         f"Inside {where} — ask the sewer provider whether a line is available",
                                         "Spokane County boundaries"))
        else:
            ev["septic"].append(Evidence("required", MEDIUM,
                                         "Outside city limits and urban growth areas (no public sewer) — "
                                         "on-site septic needs SRHD soil logs / design approval",
                                         "Spokane County boundaries"))
    elif state:
        county = parcel.county if parcel else ""
        district = HEALTH_DISTRICTS.get(county, "the local health district")
        ev["septic"].append(Evidence("required", LOW,
                                     f"{county + ' County' if county else 'Parcel'} is outside the Spokane County "
                                     f"sewer/city data used here — assume septic unless inside a city; "
                                     f"permits via {district}",
                                     "Location"))
    if facts.get("aquifer"):
        flags.append("Over the Spokane Valley–Rathdrum Prairie Aquifer (critical aquifer recharge area): "
                     "stricter septic density/design rules")

    # ---- access --------------------------------------------------------------------
    rd = facts.get("roads")
    if rd is not None:
        roads = rd["roads"]
        src = "Road centerlines vs. parcel boundary"
        tol = ROAD_FRONTAGE_TOLERANCE_M if rd["has_shape"] else 60
        touching = [r for r in roads if r["dist_m"] <= tol]
        conf = MEDIUM if rd["has_shape"] else LOW
        if touching:
            pub = [r for r in touching if r["public"]]
            if pub:
                r = pub[0]
                j = r.get("jurisdiction")
                ev["access"].append(Evidence(
                    "public_road", HIGH if (j and rd["has_shape"]) else conf,
                    f"Fronts {r['name']} ({j or r['class']})", src))
            else:
                r = touching[0]
                if r["class"] == "Easement":
                    status = "easement"
                elif r["private_hint"]:
                    status = "private_road"
                else:
                    status = "road_frontage"
                ev["access"].append(Evidence(status, conf,
                                             f"Touches {r['name']} ({r.get('jurisdiction') or r['class']})", src))
        elif roads:
            r = roads[0]
            ev["access"].append(Evidence(
                "landlocked", conf,
                f"No road touches the parcel; nearest is {r['name']} {r['dist_m'] / FT:,.0f} ft away — "
                "needs a recorded access easement", src))
        else:
            ev["access"].append(Evidence("landlocked", conf, "No mapped road within 0.5 mile", src))

    # ---- misc flags ------------------------------------------------------------------
    if facts.get("flood"):
        flags.append(f"FEMA flood hazard zone {', '.join(facts['flood'])}")
    if not parcel and listing.lat is not None:
        flags.append("No parcel found at the map pin — listing location may be approximate")
    if parcel and parcel.match.startswith("nearest"):
        flags.append("Parcel matched from the map pin by proximity — confirm the parcel number")
    if acres is not None and acres < 1:
        flags.append(f"{acres:.2f} acres: under 1 acre may be too small for a private well + septic "
                     "(WAC 246-272A minimum land area)")
    return ev, flags


IMPROVED_USE_WORDS = ("unit", "residen", "household", "dwelling", "mobile", "manufactured",
                      "commercial", "retail", "industrial", "office", "church", "school")


def _improved(land_use):
    """Assessor use codes like 'Single Unit' mean a building (which has power);
    'Vacant Land', 'Cur - Use - Ag' or 'Timber' do not."""
    lu = (land_use or "").lower()
    return bool(lu) and "vacant" not in lu and any(w in lu for w in IMPROVED_USE_WORDS)


def _split_lot(listing, parcel):
    """Parent parcel acres when the listing looks like a lot carved out of it."""
    if not parcel or not listing.lot_acres:
        return None
    pa = None
    if parcel.geometry and parcel.geometry.get("rings"):
        pa = geo.polygon_area_acres(parcel.geometry["rings"])
    pa = pa or parcel.acres
    if pa and pa > max(2.5 * listing.lot_acres, listing.lot_acres + 1):
        return pa
    return None


def _well_stats(wells):
    if not wells["nearby_count"]:
        return ""
    parts = [f"{wells['nearby_count']} water wells within 1 mi"]
    if wells["median_depth_ft"]:
        parts.append(f"median depth {wells['median_depth_ft']:.0f} ft")
    if wells["median_gpm"]:
        parts.append(f"median yield {wells['median_gpm']:g} gpm")
    if wells["median_static_ft"]:
        parts.append(f"static water level ~{wells['median_static_ft']:.0f} ft")
    return ", ".join(parts)


def analyze(listing, facts):
    text_ev = remarks.scan_text(listing.remarks)
    field_ev = remarks.from_structured(listing)
    gis_ev, flags = gis_evidence(listing, facts)

    findings, score = {}, 0
    for cat in CATEGORIES:
        evidence = field_ev[cat] + text_ev[cat] + gis_ev[cat]
        f = _pick(cat, evidence)
        findings[cat] = f
        score += _points(cat, f)
        statuses = {e.status for e in evidence if conf_rank(e.confidence) >= 2}
        if _conflict(cat, statuses):
            flags.append(f"Conflicting {cat} info: " + "; ".join(
                f"{e.source}: {CATEGORIES[cat].get(e.status, (e.status,))[0]}" for e in evidence
                if e.status in statuses))
    if any(e.status == "water_rights" for e in text_ev["water"]):
        flags.append("Listing mentions water rights")

    parcel = facts.get("parcel")
    acres = listing.lot_acres or (parcel.acres if parcel else None)
    dist = (geo.miles_between(SPOKANE_LAT, SPOKANE_LON, listing.lat, listing.lon)
            if listing.lat is not None and listing.lon is not None else None)
    return {
        "id": listing.id,
        "source": listing.source,
        "address": listing.address or (parcel.site_address if parcel else ""),
        "city": listing.city,
        "state": listing.state or facts.get("state", ""),
        "zip": listing.zip,
        "price": listing.price,
        "acres": round(acres, 2) if acres else None,
        "price_per_acre": round(listing.price / acres) if listing.price and acres else None,
        "miles_from_spokane": round(dist, 1) if dist is not None else None,
        "lat": listing.lat,
        "lon": listing.lon,
        "url": listing.url,
        "mls": listing.mls,
        "status": listing.status,
        "days_on_market": listing.days_on_market,
        "parcel_id": parcel.parcel_id if parcel else listing.parcel_id,
        "county": parcel.county if parcel else "",
        "parcel_match": parcel.match if parcel else "",
        "owner": parcel.owner if parcel else "",
        "land_use": parcel.land_use if parcel else "",
        "zoning": ", ".join(facts.get("zoning") or []),
        **{cat: dict(findings[cat].to_dict(), points=_points(cat, findings[cat])) for cat in CATEGORIES},
        "score": score,
        "rating": "Build-ready" if score >= 80 else "Needs work" if score >= 50 else "High risk",
        "shape": parcel_sketch(parcel),
        "flags": flags,
        "errors": facts.get("errors", []),
        "notes": facts.get("notes", []),
        "remarks": listing.remarks,
    }


def parcel_sketch(parcel, max_points=48):
    """Parcel outline for the app's card thumbnail: the largest ring as
    integer meters (x east, y north) around its centroid, simplified."""
    if not parcel or not parcel.geometry or not parcel.geometry.get("rings"):
        return None
    ring = max(parcel.geometry["rings"], key=len)
    lat0, lon0 = geo.polygon_centroid([ring])
    k = math.cos(math.radians(lat0)) * 111320.0
    pts = [((x - lon0) * k, (y - lat0) * 111320.0) for x, y in ring]
    span = max(max(p[0] for p in pts) - min(p[0] for p in pts),
               max(p[1] for p in pts) - min(p[1] for p in pts)) or 1.0
    tol = span * 0.01
    while True:
        out = _dp(pts, tol)
        if len(out) <= max_points:
            break
        tol *= 1.6
    return [[round(x), round(y)] for x, y in out]


def _dp(pts, tol):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        d = (abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm) if norm else math.hypot(x - x1, y - y1)
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return _dp(pts[: idx + 1], tol)[:-1] + _dp(pts[idx:], tol)


_CONFLICTS = {
    "water": [({"well", "public", "shared_well"}, {"none"})],
    "electric": [({"on_site", "at_road"}, {"none"})],
    "septic": [({"approved", "installed", "sewer"}, {"failed"})],
    "access": [({"public_road", "road_frontage"}, {"landlocked"})],
}


def _conflict(cat, statuses):
    return any(statuses & a and statuses & b for a, b in _CONFLICTS[cat])
