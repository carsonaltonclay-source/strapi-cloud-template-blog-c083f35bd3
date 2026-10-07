"""Analyse properties you found anywhere (Zillow, LandWatch, Facebook
Marketplace, a For Sale By Owner sign) by parcel number, address, listing
link or coordinates.

Text file, one property per line, optionally followed by a comma and a price:
    45321.9012, 89000
    P0512001002A
    12345 N Example Rd, Deer Park, WA 99006 | 120000
    https://www.zillow.com/homedetails/NKA-E-Example-Rd-Chattaroy-WA-99003/12345_zpid/
    47.912, -117.401
Use " | price" after an address (addresses contain commas).
"""

import json
import re
import urllib.parse

from ..models import Listing
from .csv_import import parse_float

PARCEL_RX = re.compile(r"^(\d{5}\.\d{4}|[A-Z]?\d[\d\-.A-Z]{5,24})$", re.I)
LATLON_RX = re.compile(r"^\s*(4[6-9]\.\d+)\s*,\s*(-11[6-8]\.\d+)\s*$")


def address_from_url(url):
    """Pull a street address out of common listing-site URLs."""
    path = urllib.parse.urlsplit(url).path
    m = re.search(r"/homedetails/([^/]+)/", path)               # Zillow
    if m:
        return m.group(1).replace("-", " ")
    m = re.search(r"/([A-Z]{2})/([^/]+)/([^/]+)/home/", path)    # Redfin
    if m:
        state, city, street = m.groups()
        return f"{street.replace('-', ' ')}, {city.replace('-', ' ')}, {state}"
    m = re.search(r"/realestateandhomes-detail/([^/]+)", path)   # Realtor.com
    if m:
        return m.group(1).replace("_", " ").replace("-", " ")
    return ""


def parse_entry(text, price=None, entry_id=None, source="added"):
    """One free-form entry -> Listing (parcel id, coordinates or address)."""
    text = text.strip()
    lid = f"{source}:{entry_id or text[:60]}"
    l = Listing(source=source, id=lid, price=price)
    if text.lower().startswith("http"):
        l.url = text
        l.address = address_from_url(text)
        m = re.search(r"\b(?:WA|ID)\s+(\d{5})\b", l.address)
        l.zip = m.group(1) if m else ""
    elif LATLON_RX.match(text):
        lat, lon = LATLON_RX.match(text).groups()
        l.lat, l.lon = float(lat), float(lon)
    elif PARCEL_RX.match(text.replace(" ", "")) and not re.search(r"[a-z]{3,}\s", text, re.I):
        l.parcel_id = text.replace(" ", "")
    else:
        l.address = text
    return l


def load_parcel_ids(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            # "# note" and "  # note" are comments; "Lot #4" is part of an address.
            line = line.strip() if line.lstrip().lower().startswith("http") else re.sub(r"(^|\s)#(\s.*)?$", "", line).strip()
            if not line:
                continue
            if "|" in line:
                text, price = line.rsplit("|", 1)
                out.append(parse_entry(text, parse_float(price), source="parcel"))
                continue
            parts = [p.strip() for p in line.split(",")]
            if LATLON_RX.match(line) or len(parts) > 2 or not PARCEL_RX.match(parts[0]):
                out.append(parse_entry(line, source="parcel"))
            else:
                out.append(parse_entry(parts[0], parse_float(parts[1]) if len(parts) > 1 else None,
                                       source="parcel"))
    return out


def load_requests(path):
    """JSON list of {"id", "text", "price"?, "note"?} — properties people
    added in the hosted app."""
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    out = []
    for it in items:
        if not str(it.get("text") or "").strip():
            continue
        l = parse_entry(it["text"], parse_float(it.get("price")), entry_id=it["id"])
        l.remarks = it.get("note", "") or ""
        out.append(l)
    return out
