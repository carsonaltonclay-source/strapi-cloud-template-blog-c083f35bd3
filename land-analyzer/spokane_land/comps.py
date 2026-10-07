"""Is the asking price fair? Recent vacant-land sales near the parcel.

Spokane County publishes the last sale (price, date, deed type) of every
parcel. Sales of parcels that are still vacant land, recorded by warranty
deed in the last three years, within 3 miles (widening to 6), of a similar
size (0.4x–2.5x the acreage) are the comparables. A deed that covered
several parcels is counted once, with their acreage added up.

Washington's other counties don't publish sale prices in a queryable form,
and Idaho does not disclose sale prices at all, so elsewhere this returns
None and the app falls back to comparing against other listings.
"""

import datetime
import statistics

from . import arcgis, geo
from .config import LAYERS
from .http import HttpError

YEARS = 3
SIZE_BAND = (0.4, 2.5)
MIN_COMPS = 4
VARIED_SPREAD = 3.5


def _sales(lat, lon, radius_m, since):
    feats = arcgis.query(
        LAYERS["sc_property"], geometry=(lon, lat), distance_m=radius_m,
        where=("prop_use_desc = 'Vacant Land' AND gross_sale_price > 5000 "
               f"AND document_date >= DATE '{since}' AND transfer_type LIKE '%Warranty%'"),
        out_fields="PID_NUM,gross_sale_price,document_date,acreage,excise_nbr")
    deals = {}
    for f in feats:
        a = f["attributes"]
        if not a.get("acreage"):
            continue
        key = a.get("excise_nbr") or a["PID_NUM"]
        d = deals.setdefault(key, {"price": a["gross_sale_price"], "acres": 0.0, "pids": [], "excise": a.get("excise_nbr"),
                                   "date": datetime.date.fromtimestamp(a["document_date"] / 1000).isoformat(),
                                   "lat": None, "lon": None})
        d["acres"] += a["acreage"]
        d["pids"].append(a["PID_NUM"])
    return _whole_deals(deals)


def _whole_deals(deals):
    """A deed can cover parcels outside the search circle or with buildings on them. Look up every
    parcel in each multi-parcel sale: use the full acreage, and drop sales that included buildings."""
    keys = [k for k in deals if deals[k].get("excise")]
    for i in range(0, len(keys), 80):
        batch = keys[i:i + 80]
        try:
            feats = arcgis.query(LAYERS["sc_property"], where="excise_nbr IN (" + ",".join(f"'{k}'" for k in batch) + ")",
                                 out_fields="PID_NUM,acreage,prop_use_desc,excise_nbr")
        except HttpError:
            continue
        seen = {}
        for f in feats:
            a = f["attributes"]
            seen.setdefault(a["excise_nbr"], []).append(a)
        for k, parcels in seen.items():
            d = deals.get(k)
            if not d:
                continue
            if any((p.get("prop_use_desc") or "") != "Vacant Land" for p in parcels):
                d["mixed"] = True
            d["acres"] = sum(p.get("acreage") or 0 for p in parcels) or d["acres"]
            d["pids"] = sorted({p["PID_NUM"] for p in parcels} | set(d["pids"]))
    return [d for d in deals.values() if not d.get("mixed") and d["acres"]]


def _pct(vals, q):
    vals = sorted(vals)
    k = (len(vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def comparable_sales(lat, lon, acres, parcel_id=None, today=None):
    if lat is None or not acres:
        return None
    today = today or datetime.date.today()
    since = (today - datetime.timedelta(days=365 * YEARS)).isoformat()
    lo, hi = acres * SIZE_BAND[0], acres * SIZE_BAND[1]
    comps, radius_mi = [], 3
    for radius_mi in (3, 6):
        try:
            deals = _sales(lat, lon, radius_mi * 1609.34, since)
        except HttpError:
            return None
        comps = [d for d in deals if lo <= d["acres"] <= hi and parcel_id not in d["pids"]]
        if len(comps) >= MIN_COMPS:
            break
    if len(comps) < MIN_COMPS:
        return {"n": len(comps), "radius_mi": radius_mi, "est_value": None, "examples": _examples(comps, lat, lon)}
    ppa = [d["price"] / d["acres"] for d in comps]
    med = statistics.median(ppa)
    if acres < 1:
        # Small lots sell per lot, not per acre: compare sale prices directly.
        prices = [d["price"] for d in comps]
        est, low, high, by = statistics.median(prices), _pct(prices, 0.25), _pct(prices, 0.75), "lot"
    else:
        est, low, high, by = med * acres, _pct(ppa, 0.25) * acres, _pct(ppa, 0.75) * acres, "acre"
    return {
        "n": len(comps),
        "radius_mi": radius_mi,
        "years": YEARS,
        "by": by,
        "median_ppa": round(med),
        "est_value": round(est, -2),
        "low": round(low, -2),
        "high": round(high, -2),
        # Middle half of sales spans more than 3.5x: too scattered to call a price high or low.
        "varied": bool(low and high / low > VARIED_SPREAD),
        "examples": _examples(comps, lat, lon),
    }


def _locate(comps):
    """The sales layer has no shapes; look the parcels up in the parcel layer."""
    pids = sorted({d["pids"][0] for d in comps})
    if not pids:
        return
    try:
        feats = arcgis.query(LAYERS["sc_parcels"], where="PID_NUM IN (" + ",".join(f"'{p}'" for p in pids) + ")",
                             out_fields="PID_NUM", return_geometry=True)
    except HttpError:
        return
    where = {f["attributes"]["PID_NUM"]: geo.polygon_centroid(f["geometry"]["rings"])
             for f in feats if (f.get("geometry") or {}).get("rings")}
    for d in comps:
        d["lat"], d["lon"] = where.get(d["pids"][0], (None, None))


def _examples(comps, lat, lon, k=5):
    _locate(comps)
    for d in comps:
        d["mi"] = round(geo.miles_between(lat, lon, d["lat"], d["lon"]), 1) if d["lat"] is not None else None
    near = sorted(comps, key=lambda d: (d["mi"] is None, d["mi"] or 0))[:k]
    return [{"price": round(d["price"]), "acres": round(d["acres"], 2), "date": d["date"], "mi": d["mi"],
             "pid": d["pids"][0]} for d in near]


def assessed_value(parcel_id):
    """Spokane County assessed land value (current assessment year)."""
    try:
        feats = arcgis.query(LAYERS["sc_assessed"], where=f"PID_NUM = '{parcel_id}'",
                             out_fields="land_value,asmt_year", max_records=1)
    except HttpError:
        return None
    if feats and feats[0]["attributes"].get("land_value"):
        a = feats[0]["attributes"]
        return {"land_value": a["land_value"], "year": a.get("asmt_year"), "source": "Spokane County Assessor"}
    return None


def price_verdict(price, comps, assessed, days_on_market, price_cut):
    """Short verdict plus a negotiating-room hint."""
    if not price:
        return None
    out = {}
    if comps and comps.get("est_value") and comps.get("varied"):
        out["label"] = "Nearby sales vary too much to judge"
    elif comps and comps.get("est_value"):
        ratio = price / comps["est_value"]
        out["ratio"] = round(ratio, 2)
        out["label"] = ("Well below recent sales" if ratio <= 0.8 else "Below recent sales" if ratio <= 0.95
                        else "In line with recent sales" if ratio <= 1.1 else "Above recent sales" if ratio <= 1.35
                        else "Well above recent sales")
    if assessed and assessed.get("land_value"):
        out["vs_assessed"] = round(price / assessed["land_value"], 2)
    signals = []
    if days_on_market is not None and days_on_market >= 180:
        signals.append(f"listed {days_on_market} days")
    if price_cut:
        signals.append(f"price already cut ${price_cut:,.0f}")
    if out.get("ratio", 0) > 1.1:
        signals.append(f"{round((out['ratio'] - 1) * 100)}% above nearby sales")
    if out.get("vs_assessed", 0) > 1.5:
        signals.append(f"{out['vs_assessed']:.1f}× the county's assessed value")
    if signals:
        out["negotiate"] = signals
    return out or None
