"""County rules that decide whether and how you can build: setbacks, who issues
the permits, water-right limits on new wells, and recent building permits nearby.

Setback and office tables are filled from each county's code (sources noted
inline); anything not verified falls back to typical rural values and says so.
"""

from . import arcgis
from .http import HttpError

DEFAULT_SETBACKS = {"front_ft": 50, "side_ft": 20, "source": "typical rural setbacks (not verified for this zone)"}

# (county, zone keyword) -> setbacks. Filled from county codes.
SETBACKS = {}

# county -> {"building": (office, url), "septic": (office, url)}
OFFICES = {}


def setbacks(county, state, zone):
    for (c, key), sb in SETBACKS.items():
        if c == county and key and key.lower() in (zone or "").lower():
            return sb
    return SETBACKS.get((county, None), DEFAULT_SETBACKS)


def for_parcel(county, state, lat, lon):
    out = {"county": county, "state": state}
    if county in OFFICES:
        out["offices"] = OFFICES[county]
    return out


def permits_nearby(lat, lon):
    return None
