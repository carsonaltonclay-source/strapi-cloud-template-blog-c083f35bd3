"""Rough 'all-in' cost: asking price plus what it takes to get the lot ready
for a house (water, power, septic, driveway/access, site prep).

These are planning-level Spokane-area figures, not quotes. The hosted app
uses the same defaults and lets you change them (app/app_template.html,
COST_DEFAULTS) — keep the two in sync.
"""

import math

DEFAULTS = {
    "well_per_ft": 75,          # drilling + casing, $/ft
    "well_system": 10000,       # pump, pressure tank, trench to house
    "default_well_ft": 300,     # used when no nearby well logs
    "water_hookup": 15000,      # public water connection / meter fees
    "shared_well": 5000,
    "power_service": 4000,      # transformer + service, net of the utility's allowance (Avista WA Sched. 51)
    "power_per_ft": 15,         # line extension per ft past ~150 ft of service drop (Avista WA Sch. 51, 2026:
                                # primary $12.04/ft overhead, $14.31/ft underground incl. trench, plus a fixed
                                # charge; a residential allowance up to $7,210 offsets service/transformer first)
    "offgrid_solar": 45000,
    "septic_conventional": 18000,
    "septic_standard": 22000,   # gravity system, design + permit, soils not documented
    "septic_pressure": 28000,   # pressure distribution for gravelly / rocky soils (common here)
    "septic_engineered": 40000, # mound / sand-filter system for shallow, wet or tight soils
    "sewer_hookup": 12000,
    "driveway_base": 2500,      # approach permit, culvert, first stretch
    "driveway_per_ft": 25,      # gravel driveway with base rock, $/ft
    "default_driveway_ft": 150, # when the house site isn't known
    "easement_extra": 4000,     # recording / legal for access by easement
    "landlocked_access": 25000, # buying an easement and building a road (very uncertain)
    "prep_flat": 3000, "prep_rolling": 7000, "prep_steep": 20000, "prep_very_steep": 35000,
}

def _round(x):
    """Round half up, like the app's Math.round (Python's round() rounds half to even)."""
    return int(math.floor(x + 0.5))


POWER_DISTANCE_GUESS = {"likely_near": 600, "possible": 1500, "far": 3000}


def _water(r, wells, d):
    st = r["water"]["status"]
    if st == "well":
        return 0, "Well already on the parcel"
    if st == "public":
        return d["water_hookup"], "Public water connection"
    if st == "shared_well":
        return d["shared_well"], "Shared-well hookup"
    if st == "public_area":
        return d["water_hookup"], "Water-district hookup (if a main reaches the lot)"
    depth = (wells or {}).get("median_depth_ft") or d["default_well_ft"]
    return _round(depth * d["well_per_ft"] + d["well_system"]), f"Drill a well (~{depth:.0f} ft like nearby wells)"


def _power(r, neighbors, d):
    st = r["electric"]["status"]
    if st == "on_site":
        return 0, "Power already on the parcel"
    if st == "at_road":
        return d["power_service"], "Service from the road"
    if st == "none":
        return d["offgrid_solar"], "Off-grid solar system"
    dist = (neighbors or {}).get("nearest_ft") or POWER_DISTANCE_GUESS.get(st, 1500)
    extra = max(0, dist - 150)
    return _round(d["power_service"] + extra * d["power_per_ft"]), f"Extend power ~{dist:,.0f} ft"


def _septic(r, soil, d):
    st = r["septic"]["status"]
    if st == "installed":
        return 0, "Septic system already installed"
    if st == "sewer":
        return d["sewer_hookup"], "Sewer hookup"
    if st == "sewer_area":
        return d["sewer_hookup"], "Sewer hookup (if a line is available)"
    outlook = (soil or {}).get("outlook")
    if st == "failed":
        return d["septic_engineered"], "Engineered septic (failed perc)"
    if st == "approved":
        return d["septic_conventional"], "Septic per approved design"
    if outlook == "hard":
        return d["septic_engineered"], "Engineered / mound septic (shallow bedrock, wet or tight soils)"
    if outlook == "design":
        return d["septic_pressure"], "Pressure-distribution septic (fast-draining or rocky soils)"
    return d["septic_standard"], "Standard septic system"


def _access(r, site, d):
    st = r["access"]["status"]
    ft = ((site or {}).get("site") or {}).get("driveway_ft") or d["default_driveway_ft"]
    drive = d["driveway_base"] + ft * d["driveway_per_ft"]
    measured = ((site or {}).get("site") or {}).get("driveway_ft") is not None
    what = f"{ft:,.0f} ft driveway to the best house site" if measured else f"Driveway (~{ft:,.0f} ft assumed)"
    if st == "landlocked":
        return _round(d["landlocked_access"] + drive), "Buy an access easement + build a road (very uncertain)"
    if st == "easement":
        return _round(drive + d["easement_extra"]), what + " + easement paperwork"
    if st == "seasonal":
        return _round(drive * 1.5), what + " + road improvements"
    if st == "private_road":
        return _round(drive * 1.15), what + " off a private road"
    return _round(drive), what


def _prep(terrain, site, d):
    sp = ((site or {}).get("site") or {}).get("slope_pct")
    if sp is not None:
        cls = "flat" if sp < 8 else "rolling" if sp < 15 else "steep" if sp < 25 else "very_steep"
    else:
        cls = (terrain or {}).get("slope_class")
    key = {"rolling": "prep_rolling", "steep": "prep_steep", "very_steep": "prep_very_steep"}.get(cls, "prep_flat")
    return d[key], {"prep_rolling": "Grading on rolling ground", "prep_steep": "Grading + foundation work on a steep lot",
                    "prep_very_steep": "Major grading on very steep ground"}.get(key, "Clearing and building pad")


def all_in(result, facts, defaults=None):
    d = dict(DEFAULTS, **(defaults or {}))
    items = [
        ("water",) + _water(result, facts.get("wells"), d),
        ("power",) + _power(result, facts.get("neighbors"), d),
        ("septic",) + _septic(result, facts.get("soil"), d),
        ("access",) + _access(result, facts.get("site"), d),
        ("prep",) + _prep(facts.get("terrain"), facts.get("site"), d),
    ]
    extra = sum(c for _, c, _ in items)
    price = result.get("price")
    return {
        "items": [{"key": k, "cost": c, "label": lab} for k, c, lab in items],
        "improvements": extra,
        "total": (price or 0) + extra if price else None,
        # inputs the app needs to recompute with the viewer's own assumptions
        "inputs": {"well_ft": (facts.get("wells") or {}).get("median_depth_ft"),
                   "power_ft": (facts.get("neighbors") or {}).get("nearest_ft"),
                   "soil": (facts.get("soil") or {}).get("outlook"),
                   "driveway_ft": (((facts.get("site") or {}).get("site")) or {}).get("driveway_ft"),
                   "site_slope": (((facts.get("site") or {}).get("site")) or {}).get("slope_pct")},
    }
