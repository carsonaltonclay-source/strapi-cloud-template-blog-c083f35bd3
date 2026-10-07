"""Is the asking price fair, outside Spokane County? Recent land sales from a sold-listings file.

Spokane County publishes every sale (see comps.py). Stevens, Lincoln, Pend Oreille and Whitman counties
don't, but land sold through the MLS shows up on listing sites with its sale price, date, acreage and
location. A CSV of those sales (columns: price, sold_date, acres, lat, lon, url, zpid) is matched the
same way as county sales: sold in the last three years, 0.4x-2.5x the acreage, within 3 miles,
widening to 6 and then 10 (rural sales are sparse).

Idaho is a non-disclosure state: sale prices aren't public and listing sites hide them, so Idaho
parcels get the county's assessed market value instead (comps.kootenai_assessed).
"""

import csv
import datetime
import statistics

from . import geo
from .comps import MIN_COMPS, SIZE_BAND, VARIED_SPREAD, YEARS, _pct

RADII_MI = (3, 6, 10)
SOURCE = "MLS land sales (via Zillow sold listings)"


def load(path):
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"price": float(r["price"]), "acres": float(r["acres"]), "lat": float(r["lat"]),
                            "lon": float(r["lon"]), "date": r["sold_date"], "url": r.get("url") or "",
                            "zpid": str(r.get("zpid") or "")})
            except (KeyError, TypeError, ValueError):
                continue
    return [s for s in out if s["price"] > 5000 and s["acres"] > 0]


def comparable(lat, lon, acres, sales, listing_mls="", today=None):
    if lat is None or not acres or not sales:
        return None
    today = today or datetime.date.today()
    since = (today - datetime.timedelta(days=365 * YEARS)).isoformat()
    lo, hi = acres * SIZE_BAND[0], acres * SIZE_BAND[1]
    own = listing_mls.rsplit("-", 1)[-1] if listing_mls else ""
    pool = [s for s in sales if s["date"] >= since and lo <= s["acres"] <= hi and s["zpid"] != own]
    for s in pool:
        s["_mi"] = geo.miles_between(lat, lon, s["lat"], s["lon"])
    comps, radius = [], RADII_MI[0]
    for radius in RADII_MI:
        comps = [s for s in pool if s["_mi"] <= radius]
        if len(comps) >= MIN_COMPS:
            break
    examples = [{"price": round(s["price"]), "acres": round(s["acres"], 2), "date": s["date"], "mi": round(s["_mi"], 1),
                 "url": s["url"]} for s in sorted(comps, key=lambda s: s["_mi"])[:5]]
    if len(comps) < MIN_COMPS:
        return {"n": len(comps), "radius_mi": radius, "est_value": None, "examples": examples, "source": SOURCE}
    ppa = [s["price"] / s["acres"] for s in comps]
    if acres < 1:
        prices = [s["price"] for s in comps]
        est, low, high, by = statistics.median(prices), _pct(prices, 0.25), _pct(prices, 0.75), "lot"
    else:
        est, low, high, by = statistics.median(ppa) * acres, _pct(ppa, 0.25) * acres, _pct(ppa, 0.75) * acres, "acre"
    return {"n": len(comps), "radius_mi": radius, "years": YEARS, "by": by, "median_ppa": round(statistics.median(ppa)),
            "est_value": round(est, -2), "low": round(low, -2), "high": round(high, -2),
            "varied": bool(low and high / low > VARIED_SPREAD), "examples": examples, "source": SOURCE}
