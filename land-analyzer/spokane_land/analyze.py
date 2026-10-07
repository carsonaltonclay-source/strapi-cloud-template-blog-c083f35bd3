"""Turn listing text + GIS facts into a verdict per category and a score."""

import math
import re

from . import buildability, cost, geo, remarks

from .config import FRONTAGE_HIGHWAY_M, FRONTAGE_LOCAL_M, ROAD_FRONTAGE_TOLERANCE_M, SPOKANE_LAT, SPOKANE_LON
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
    "Lincoln": "Lincoln County Public Health",
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
    ranked = [e for e in evidence if e.status in order]   # notes like "water rights" aren't a status
    if not ranked:
        return Finding(evidence=sorted(evidence, key=lambda e: -conf_rank(e.confidence)))
    best = max(ranked, key=lambda e: (conf_rank(e.confidence), -order.index(e.status)))
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
        districts_unknown = "water_district" in facts and facts["water_district"] is None
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
        if not wells["on_parcel"] and not districts and districts_unknown:
            ev["water"].append(Evidence(
                "needs_well", LOW, "No well log for this parcel; the public water map didn't answer, so a water "
                "main can't be ruled out" + (". " + stats if stats else ""), src_w))
        elif not wells["on_parcel"] and not districts:
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
        def tol(r):
            if not rd["has_shape"]:
                return 60
            highway = re.search(r"\b(SR|Hwy|Highway|US|I-\d+|Interstate|State\s+Route)\b", r["name"] or "", re.I) \
                or "highway" in (r.get("class") or "").lower()
            return FRONTAGE_HIGHWAY_M if highway else FRONTAGE_LOCAL_M
        touching = sorted((r for r in roads if r["dist_m"] <= tol(r)), key=lambda r: r["dist_m"])
        conf = MEDIUM if rd["has_shape"] else LOW
        if touching:
            pub = [r for r in touching if r["public"]]
            if pub:
                r = pub[0]
                j = r.get("jurisdiction")
                near = r["dist_m"] <= ROAD_FRONTAGE_TOLERANCE_M
                ev["access"].append(Evidence(
                    "public_road", HIGH if (j and rd["has_shape"] and near) else conf,
                    f"Fronts {r['name']} ({j or r['class']})" + ("" if near else
                    f"; the lot line is ~{r['dist_m'] / FT:,.0f} ft from the road's centerline, within a typical right-of-way"), src))
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

    # ---- terrain ---------------------------------------------------------------------
    t = facts.get("terrain")
    if t and t["slope_class"] in ("steep", "very_steep") and (t["flat_pct"] or 0) < 25:
        flags.append(f"Steep ground: average slope {t['slope_mean_pct']}%, only {t['flat_pct'] or 0}% of the "
                     "parcel is gentle enough to build on easily")

    # ---- misc flags ------------------------------------------------------------------
    if facts.get("flood"):
        flags.append(f"FEMA flood hazard zone {', '.join(facts['flood'])}")
    flags += [n for n in facts.get("notes", []) if n.startswith("Map services didn't answer")]
    if not parcel and listing.lat is not None:
        flags.append("No parcel found at the map pin — listing location may be approximate")
    if parcel and parcel.match.startswith("nearest"):
        flags.append("Parcel matched from the map pin by proximity — confirm the parcel number")
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

    soil = facts.get("soil")
    if soil and soil.get("outlook") == "hard" and findings["septic"].status not in ("installed", "sewer", "approved"):
        flags.append(f"USDA soil survey: {', '.join(soil['hard_limits']).lower()} — septic may need a mound or "
                     "engineered system; get a perc test before buying")
    fire = facts.get("wildfire")
    if fire and fire.get("class") in (4, 5):
        flags.append(f"{fire['label']} wildfire hazard (USFS): plan defensible space, ask the county whether "
                     "wildland-urban-interface building rules apply, and expect higher insurance")

    parcel = facts.get("parcel")
    split = _split_lot(listing, parcel)
    acres = listing.lot_acres or (parcel.acres if parcel else None)
    dist = (geo.miles_between(SPOKANE_LAT, SPOKANE_LON, listing.lat, listing.lon)
            if listing.lat is not None and listing.lon is not None else None)
    if acres is not None and acres < 1 and findings["water"].status not in ("public", "public_area") \
            and findings["septic"].status not in ("sewer", "sewer_area"):
        flags.append(f"{acres:.2f} acres: below the state minimum for a new lot on a private well + septic "
                     "(1 acre, or 2 acres in fine soils — WAC 246-272A-0320). Existing lots of record may still "
                     "qualify; ask the health district")
    zcheck = buildability.zoning_check(", ".join(facts.get("zoning") or []), acres) or facts.get("zoning_other")
    if zcheck and zcheck["status"] == "not_residential":
        flags.append(zcheck["label"])
    elif zcheck and zcheck["status"] == "undersized":
        flags.append(zcheck["label"] + " — confirm it is a buildable lot of record")
    result = {
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
        "terrain": facts.get("terrain"),
        "soil": soil,
        "wildfire": fire,
        "internet": facts.get("internet"),
        "power_company": facts.get("power_company"),
        "zoning_check": zcheck,
        "checks": derived_checks(findings, zcheck, facts, listing.lot_acres, split) + list(facts.get("checks") or []),
        "site": facts.get("site"),
        "cell": facts.get("cell"),
        "school_district": facts.get("school_district"),
        "rules": facts.get("rules"),
        "permits": facts.get("permits"),
        "comps": facts.get("comps"),
        # A lot split from a bigger parcel: the parent's assessed value isn't this lot's.
        "assessed": None if split else facts.get("assessed"),
        "flags": flags,
        "errors": facts.get("errors", []),
        "notes": facts.get("notes", []),
        "remarks": listing.remarks,
    }
    result["cost"] = cost.all_in(result, facts)
    result["buildable"] = buildable_verdict(result)
    return result


def derived_checks(findings, zcheck, facts, listed_acres=None, split=None):
    """Deal-breaker entries for things the main analysis already found."""
    out = []
    acc = findings["access"]
    if acc.status == "landlocked":
        out.append({"key": "access", "level": "bad", "label": "No legal road access shown",
                    "detail": acc.evidence[0].detail if acc.evidence else "", "source": "Road map vs. parcel boundary",
                    "ask": "Is there a recorded access easement to a public road? Can I see it on the title report?"})
    elif acc.status in ("easement", "private_road", "seasonal"):
        out.append({"key": "access", "level": "warn", "label": CATEGORIES["access"][acc.status][0],
                    "detail": "make sure a recorded easement gives legal access and covers utilities",
                    "source": "Road map vs. parcel boundary",
                    "ask": "Is the access easement recorded, and does it allow utilities? Is there a road maintenance agreement?"})
    elif acc.status in ("public_road", "road_frontage"):
        out.append({"key": "access", "level": "ok", "label": "Touches a road", "detail": acc.evidence[0].detail if acc.evidence else "",
                    "source": "Road map vs. parcel boundary"})
    if findings["septic"].status == "failed":
        out.append({"key": "perc", "level": "bad", "label": "Failed perc test reported", "source": "listing"})
    if zcheck and zcheck["status"] == "not_residential":
        out.append({"key": "zoning", "level": "bad", "label": zcheck["label"], "detail": zcheck["detail"], "source": "zoning"})
    elif zcheck and zcheck["status"] == "undersized":
        out.append({"key": "zoning", "level": "warn", "label": zcheck["label"], "detail": zcheck["detail"], "source": "zoning",
                    "ask": "Is this a legal lot of record that can get a building permit?"})
    site = facts.get("site")
    partial = bool(site and listed_acres and site.get("parcel_acres") and site["parcel_acres"] < 0.6 * listed_acres)
    if partial:
        out.append({"key": "room", "level": "warn",
                    "label": f"Only part of this listing was checked ({site['parcel_acres']} of {listed_acres:g} acres)",
                    "detail": "the listing seems to cover more than one county parcel; building room was measured on the one the map pin hits",
                    "source": "parcel shape"})
    elif site and site.get("buildable_acres") is not None:
        ba = site["buildable_acres"]
        parent = bool(split)  # same test as the "lot split from a bigger parcel" warning
        if parent:
            # A lot carved from a bigger county parcel: the parent's numbers can't say where on it this lot sits,
            # so its best house site (driveway length, slope) isn't this lot's either.
            site["parent_acres"] = site["parcel_acres"]
            site["site"] = None
            site["listing_buildable_acres"] = round(listed_acres * site["buildable_pct"] / 100, 2)
            out.append({"key": "room", "level": "info",
                        "label": f"Building room not measured for this lot (part of a {site['parcel_acres']}-acre county parcel)",
                        "detail": f"about {site['buildable_pct']}% of the parent parcel is buildable; check this lot's own "
                                  "setbacks and slope on the plat", "source": "parcel shape"})
        lost = ", ".join(f"{k} {v} ac" for k, v in (site.get("lost_to") or {}).items())
        # On sewer + public water only the house needs room; otherwise also a well and a drainfield.
        utilities = (findings["septic"].status in ("sewer", "sewer_area")
                     and findings["water"].status in ("public", "public_area"))
        need_min, need_ok = (0.03, 0.06) if utilities else (0.1, 0.35)
        if parent:
            pass
        elif ba < need_min:
            out.append({"key": "room", "level": "bad", "label": "No room for a house after setbacks, slopes and hazards",
                        "detail": f"lost to {lost}" if lost else "", "source": "parcel shape, elevation and hazard maps"})
        elif ba < need_ok:
            out.append({"key": "room", "level": "warn", "label": f"Tight building area: about {ba} acre",
                        "detail": ("a house footprint plus yard" if utilities else
                                   "house, well (100 ft from the drainfield) and septic need roughly half an acre")
                                  + (f"; lost to {lost}" if lost else ""), "source": "parcel shape, elevation and hazard maps"})
    if site and site.get("hazards_unchecked") and not partial:
        out.append({"key": "room_hazards", "level": "info",
                    "label": "Building room doesn't account for " + ", ".join(site["hazards_unchecked"]),
                    "detail": "those hazard maps didn't answer when this was checked", "source": "hazard maps"})
    if findings["water"].status not in ("well", "public", "shared_well", "public_area"):
        out += (facts.get("rules") or {}).get("water_limits") or []
    return out


NO_BUILD_KEYS = {"zoning", "perc", "room", "unbuildable", "conservation"}


def buildable_verdict(r):
    """Is it actually buildable? yes / work / doubt / no with the reasons."""
    bad = [c for c in r.get("checks") or [] if c["level"] == "bad"]
    warn = [c for c in r.get("checks") or [] if c["level"] == "warn"]
    hard_soil = (r.get("soil") or {}).get("outlook") == "hard" and r["septic"]["status"] not in ("installed", "sewer", "approved")
    steep = ((r.get("site") or {}).get("site") or {}).get("slope_pct") or 0
    work = []
    if r["water"]["status"] in ("needs_well", "none", "well_possible", "district_wells"):
        work.append("drill a well")
    if r["electric"]["status"] in ("possible", "far", "none"):
        work.append("bring power a long way")
    if hard_soil:
        work.append("engineered septic")
    if steep > 15:
        work.append("grading on a slope")
    names = {"water": "water", "electric": "power", "septic": "septic", "access": "road access"}
    unknown = [names[c] for c in names if r[c]["status"] == "unknown"]
    if unknown:
        work.append("confirm " + ", ".join(unknown))
    if r.get("lat") is None:
        unknown.append("location")
    if any(c["key"] in NO_BUILD_KEYS for c in bad):
        level, label = "no", "Likely not buildable"
    elif bad:
        level, label = "doubt", "Questionable"
    elif len(unknown) >= 2 or r.get("lat") is None:
        level, label = "doubt", "Not enough information"
    elif not r.get("site") or r["site"].get("parent_acres"):
        level, label = "work", "Buildable with work"
        work.append("confirm where a house fits")
    elif warn or work:
        level, label = "work", "Buildable with work"
    else:
        level, label = "yes", "Buildable"
    reasons = [c["label"] for c in bad] + [c["label"] for c in warn][:3] + ([("Needs: " + ", ".join(work))] if work else [])
    return {"level": level, "label": label, "reasons": reasons[:5]}


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
