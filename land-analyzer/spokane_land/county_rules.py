"""County rules that decide whether and how you can build: setbacks, who issues
the permits, water-right limits on new wells, and new-home permits nearby.

Sources (checked 2026-10):
* Spokane County Code 14.616.300 / 14.618.300 (Tables 616-3, 618-3): 25 ft front from the
  property (right-of-way) line, 5 ft side/rear in rural and resource zones. LDR (Table 606-3,
  2018 printing, not re-verified): 15 ft front, 5 ft side.
* Pend Oreille County Building Regulations 2023 xx.84.020: 25 ft front, 5 ft side/rear.
* Lincoln County Code ch. 38 (Ord. 24-02; secs. 38-125, 38-202): AG and RES 30 ft from state/county road
  right-of-way (10 ft inside a legal subdivision), 10 ft sides.
* Kootenai County Title 8 (8.2.108, 8.2.207, 8.2.308): 25 ft front, 10 ft side.
* Bonner County BCRC 12-411 Table 4-1: R-5 / R-10 25 ft street and 25 ft property-line setbacks
  (staff reports HO0004-25, VA0001-26); A/F and Forest assumed the same; Suburban 25 / 5.
* Stevens, Whitman, Benewah: not verified -> typical values, labelled as such.
* RCW 90.94.020: WRIAs 55 (Little Spokane) and 59 (Colville) limit new permit-exempt household
  wells to domestic use, 3,000 gal/day annual average, $500 fee with the building permit.
* WAC 173-557: Spokane River instream-flow rule over the aquifer in WRIAs 54 and 57.
* Permit offices and health districts: county / district web pages loaded 2026-10.
"""

import datetime

from . import arcgis, geo
from .config import LAYERS
from .http import HttpError

DEFAULT_SETBACKS = {"front_ft": 25, "side_ft": 10, "source": "typical rural setbacks (not verified for this county)"}

# (county, zone keyword or None for the county default) -> setbacks
SETBACKS = {
    ("Spokane", "Low Density Residential"): {"front_ft": 15, "side_ft": 5, "source": "Spokane County Code Table 606-3 (2018)"},
    ("Spokane", "Residential"): {"front_ft": 15, "side_ft": 5, "source": "Spokane County Code Table 606-3 (2018)"},
    ("Spokane", None): {"front_ft": 25, "side_ft": 5, "source": "Spokane County Code 14.618.300"},
    ("Pend Oreille", None): {"front_ft": 25, "side_ft": 5, "source": "Pend Oreille County Building Regulations 2023"},
    ("Lincoln", None): {"front_ft": 30, "side_ft": 10, "source": "Lincoln County Code ch. 38 (2024)"},
    ("Kootenai", None): {"front_ft": 25, "side_ft": 10, "source": "Kootenai County Code 8.2.207"},
    ("Bonner", "Suburban"): {"front_ft": 25, "side_ft": 5, "source": "Bonner County Code 12-411"},
    ("Bonner", None): {"front_ft": 25, "side_ft": 25, "source": "Bonner County Code 12-411"},
}

# county -> building office, septic office: (name, url, phone)
OFFICES = {
    "Spokane": {"building": ("Spokane County Building & Planning", "https://www.spokanecounty.gov/247/Building-Planning", ""),
                "septic": ("Spokane Regional Health District", "https://srhd.org/programs-and-services/land-use/oss", "509-324-1560")},
    "Stevens": {"building": ("Stevens County Land Services", "https://www.stevenscountywa.gov", ""),
                "septic": ("Northeast Tri County Health District", "https://www.netchd.org/232/Septic-Permit-Applications", "509-684-2262")},
    "Pend Oreille": {"building": ("Pend Oreille County Community Development", "https://www.pendoreille.gov/community-development", "509-447-4821"),
                     "septic": ("Northeast Tri County Health District", "https://www.netchd.org/232/Septic-Permit-Applications", "509-447-3131")},
    "Lincoln": {"building": ("Lincoln County Building Division", "https://www.lincolncountywa.com/288/Building-Division", "509-721-0539"),
                "septic": ("Lincoln County Public Health", "https://www.lincolncountywa.com/218/Onsite-Sewage-Systems", "509-725-1001")},
    "Whitman": {"building": ("Whitman County Building & Development", "https://www.whitmancounty.gov/268/Building-Development-Division", "509-397-6206"),
                "septic": ("Whitman County Public Health", "https://whitmancountypublichealth.org/environmental-health/sewage-systems", "509-397-6280")},
    "Kootenai": {"building": ("Kootenai County Community Development", "https://www.kcgov.us/219/Community-Development", "208-446-1040"),
                 "septic": ("Panhandle Health District", "https://panhandlehealthdistrict.org/licensing-and-permitting/septic-permits-and-records/", "")},
    "Bonner": {"building": ("Bonner County Planning (building location permit)", "https://www.bonnercountyid.gov/departments/Planning", "208-265-1458"),
               "septic": ("Panhandle Health District, Sandpoint", "https://panhandlehealthdistrict.org/licensing-and-permitting/septic-permits-and-records/", "208-265-6384")},
    "Benewah": {"building": ("Benewah County Planning & Zoning", "https://www.benewahcountyid.gov/departments/planning-zoning", "208-967-4232"),
                "septic": ("Panhandle Health District, St. Maries", "https://panhandlehealthdistrict.org/licensing-and-permitting/septic-permits-and-records/", "")},
}

COUNTY_NOTES = {
    "Spokane": ["Septic permit fees about $1,890 (SRHD 2026: $1,000 application + $890 permit)."],
    "Bonner": ["Bonner County has no building-code permit: you get a building location permit; electrical, plumbing and "
               "mechanical permits come from the State of Idaho."],
    "Kootenai": ["Septic permit about $1,250 (Panhandle Health District FY27)."],
}

RESTRICTED_WRIAS = {55: "Little Spokane", 59: "Colville"}


TOWN_SETBACKS = {"front_ft": 15, "side_ft": 5, "source": "typical town-lot setbacks (city residential zone)"}


def setbacks(county, state, zone, town=False):
    if town:  # a lot in a city's residential zone, not the county's rural setbacks
        return TOWN_SETBACKS
    for (c, key), sb in SETBACKS.items():
        if c == county and key and key.lower() in (zone or "").lower():
            return sb
    return SETBACKS.get((county, None), DEFAULT_SETBACKS)


def _wria(lat, lon):
    try:
        feats = arcgis.query(LAYERS["wa_wria"], geometry=(lon, lat), out_fields="WRIA_NR,WRIA_NM")
    except HttpError:
        return None
    return (feats[0]["attributes"]["WRIA_NR"], feats[0]["attributes"]["WRIA_NM"]) if feats else None


def for_parcel(county, state, lat, lon, aquifer=False):
    out = {"county": county, "state": state, "notes": list(COUNTY_NOTES.get(county, [])), "water_limits": []}
    if county in OFFICES:
        out["offices"] = {k: list(v) for k, v in OFFICES[county].items()}
    if state == "WA" and lat is not None:
        w = _wria(lat, lon)
        if w:
            nr, name = w
            out["wria"] = f"{nr} {name}"
            if nr in RESTRICTED_WRIAS:
                fee = "$700 in Pend Oreille County" if county == "Pend Oreille" and nr == 55 else "$500"
                out["water_limits"].append({
                    "key": "water_rule", "level": "warn", "label": f"New wells limited here (WRIA {nr} {name})",
                    "detail": f"state streamflow law RCW 90.94: a new household well is limited to domestic use, "
                              f"3,000 gal/day on average, with a {fee} fee at building permit and a note on the title",
                    "source": "RCW 90.94.020"})
            elif aquifer or (aquifer is None and nr in (54, 57)):
                # WAC 173-557 covers the river and the area over the SVRP aquifer, not whole WRIAs.
                where = "over the Spokane aquifer" if aquifer else "if the parcel is over the Spokane aquifer (not checked)"
                out["wria_note"] = (f"WRIA {nr} {name}: {where} the state's Spokane River flow rule "
                                    "(WAC 173-557) can curtail new household wells unless mitigated; public water is preferred.")
    return out


def permits_nearby(lat, lon):
    """New-home building permits within a mile (Spokane County): open now, and issued in 2023."""
    if lat is None:
        return None
    try:
        now = arcgis.query(LAYERS["sc_permits_open"], geometry=(lon, lat), distance_m=1609,
                           where="PermitTypeDesc LIKE 'Residential New%' OR PermitTypeDesc LIKE 'Manufactured Home%' "
                                 "OR PermitTypeDesc LIKE 'Residential Dwelling%'",
                           out_fields="PermitNumber,PermitStatus,PermitSubmitDate,PermitTypeDesc")
        y23 = arcgis.query(LAYERS["sc_permits_2023"], geometry=(lon, lat), distance_m=1609,
                           where="Permit_Type LIKE 'Residential Dwelling - New%'", out_fields="Permit_Number")
    except HttpError:
        return None
    open_n = len({f["attributes"]["PermitNumber"] for f in now})
    done_n = len({f["attributes"]["Permit_Number"] for f in y23})
    latest = max((f["attributes"].get("PermitSubmitDate") or 0 for f in now), default=0)
    when = datetime.date.fromtimestamp(latest / 1000).isoformat()[:7] if latest else ""
    if not open_n and not done_n:
        summary = "No new-house permits within a mile recently (2023 and open permits) — building here is rarer; ask the county why."
    else:
        summary = (f"{done_n} new house{'s' if done_n != 1 else ''} permitted within a mile in 2023"
                   + (f"; {open_n} more open now (latest filed {when})" if open_n else "") + " — people are building nearby.")
    return {"open": open_n, "issued_2023": done_n, "latest": when, "summary": summary, "source": "Spokane County permits"}
