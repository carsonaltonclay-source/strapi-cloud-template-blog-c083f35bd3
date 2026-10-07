"""Import listings from any CSV export (MLS, Redfin, Zillow, LandWatch, a
spreadsheet you keep by hand...). Column names are matched loosely; see
ALIASES. Only a price plus either coordinates, an address or a parcel number
is needed."""

import csv
import hashlib
import re

from ..models import Listing

ALIASES = {
    "id": ["listingkey", "listing id", "listingid", "id", "mls#", "mls #", "mls number", "mlsnumber", "listnumber", "listing number"],
    "address": ["address", "unparsedaddress", "street address", "full address", "property address", "site address"],
    "city": ["city", "town"],
    "state": ["state", "stateorprovince", "state or province"],
    "zip": ["zip", "zipcode", "zip code", "postalcode", "zip or postal code", "postal code"],
    "price": ["price", "listprice", "list price", "asking price", "current price"],
    "lot_acres": ["acres", "lotsizeacres", "lot size acres", "lot acres", "acreage", "total acres"],
    "lot_sqft": ["lot size", "lotsizesquarefeet", "lot size sqft", "lot sqft", "lot size (sq ft)"],
    "lat": ["latitude", "lat", "y"],
    "lon": ["longitude", "lon", "lng", "long", "x"],
    "url": ["url", "link", "listing url", "listingurl", "virtualtoururlunbranded"],
    "status": ["status", "standardstatus", "mlsstatus"],
    "days_on_market": ["days on market", "daysonmarket", "dom", "cdom"],
    "parcel_id": ["parcelnumber", "parcel number", "parcel", "parcel id", "apn", "pid", "tax id", "taxid", "tax parcel"],
    "remarks": ["publicremarks", "public remarks", "remarks", "description", "comments", "marketing remarks", "notes"],
    "water_source": ["watersource", "water source", "water"],
    "electric": ["electric", "electricity", "power", "utilities"],
    "sewer": ["sewer", "septic", "waste", "sewer/septic"],
    "road_access": ["roadfrontagetype", "road frontage", "road access", "access", "road", "roadsurfacetype"],
}


def _norm(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("_", " "))


def _dom(v):
    """Days on market; sites use -1 for 'unknown'."""
    d = parse_int(v)
    return d if d is not None and d >= 0 else None


def parse_float(v):
    if v is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def parse_int(v):
    f = parse_float(v)
    return int(f) if f is not None else None


def map_columns(headers):
    normed = {_norm(h): h for h in headers}
    mapping = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in normed:
                mapping[field] = normed[n]
                break
        else:
            # Fallback: header starting with an alias, e.g. "URL (SEE https://...)"
            for nh, h in normed.items():
                if any(nh.startswith(n + " ") for n in names if len(n) > 2):
                    mapping[field] = h
                    break
    return mapping


def load_csv(path, source_name=None):
    source_name = source_name or "csv"
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        cols = map_columns(reader.fieldnames or [])
        out, seen = [], {}
        for i, row in enumerate(reader, 1):
            get = lambda k: (row.get(cols[k]) or "").strip() if k in cols else ""
            if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                continue
            acres = parse_float(get("lot_acres"))
            if acres is None:
                sqft = parse_float(get("lot_sqft"))
                acres = sqft / 43560.0 if sqft else None
            # Without an id column, derive a stable id from the row's content (not its position),
            # so refreshes and multiple files don't mix listings up.
            if not (get("address") or get("parcel_id") or (get("lat") and get("lon"))):
                continue  # nothing to locate the land by
            # Acres (not price, which changes) tells "TBD Elk Rd" lots apart; a counter separates exact repeats.
            base = "|".join((get("address"), get("city"), get("lat"), get("lon"), get("parcel_id"),
                             f"{acres:.2f}" if acres else "")).lower()
            seen[base] = seen.get(base, 0) + 1
            if seen[base] > 1:
                base += f"|{seen[base]}"
            lid = get("id") or "h" + hashlib.sha1(base.encode()).hexdigest()[:12]
            out.append(Listing(
                source=source_name,
                id=f"{source_name}:{lid}",
                address=get("address"),
                city=get("city"),
                state=get("state"),
                zip=get("zip"),
                price=parse_float(get("price")),
                lot_acres=acres,
                lat=parse_float(get("lat")),
                lon=parse_float(get("lon")),
                url=get("url"),
                mls=get("id"),
                status=get("status"),
                days_on_market=_dom(get("days_on_market")),
                parcel_id=get("parcel_id"),
                remarks=get("remarks"),
                water_source=get("water_source"),
                electric=get("electric"),
                sewer=get("sewer"),
                road_access=get("road_access"),
            ))
    return out
