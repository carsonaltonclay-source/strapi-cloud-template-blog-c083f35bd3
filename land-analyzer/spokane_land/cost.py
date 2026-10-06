"""Rough 'all-in' cost: asking price plus what it takes to get the lot ready
for a house (water, power, septic, driveway/access, site prep).

These are planning-level Spokane-area figures, not quotes. The hosted app
uses the same defaults and lets you change them (app/app_template.html,
COST_DEFAULTS) — keep the two in sync.
"""

DEFAULTS = {
    "well_per_ft": 75,          # drilling + casing, $/ft
    "well_system": 10000,       # pump, pressure tank, trench to house
    "default_well_ft": 300,     # used when no nearby well logs
    "water_hookup": 15000,      # public water connection / meter fees
    "shared_well": 5000,
    "power_service": 6000,      # service drop, meter base, trenching from road
    "power_per_ft": 30,         # line extension beyond the first 150 ft
    "offgrid_solar": 45000,
    "septic_conventional": 18000,
    "septic_standard": 22000,   # gravity system, design + permit, soils not documented
    "septic_pressure": 28000,   # pressure distribution for gravelly / rocky soils (common here)
    "septic_engineered": 40000, # mound / sand-filter system for shallow, wet or tight soils
    "sewer_hookup": 12000,
    "driveway": 6000,
    "easement_extra": 4000,     # recording / legal for access by easement
    "landlocked_access": 25000, # buying an easement and building a road (very uncertain)
    "prep_flat": 3000, "prep_rolling": 7000, "prep_steep": 20000, "prep_very_steep": 35000,
}

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
    return round(depth * d["well_per_ft"] + d["well_system"]), f"Drill a well (~{depth:.0f} ft like nearby wells)"


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
    return round(d["power_service"] + extra * d["power_per_ft"]), f"Extend power ~{dist:,.0f} ft"


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


def _access(r, d):
    st = r["access"]["status"]
    if st == "landlocked":
        return d["landlocked_access"], "Buy an access easement + build a road (very uncertain)"
    if st == "easement":
        return d["driveway"] + d["easement_extra"], "Driveway + easement paperwork"
    if st == "seasonal":
        return d["driveway"] * 2, "Driveway + road improvements"
    if st == "private_road":
        return round(d["driveway"] * 1.3), "Driveway off a private road"
    return d["driveway"], "Driveway"


def _prep(terrain, d):
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
        ("access",) + _access(result, d),
        ("prep",) + _prep(facts.get("terrain"), d),
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
                   "soil": (facts.get("soil") or {}).get("outlook")},
    }
