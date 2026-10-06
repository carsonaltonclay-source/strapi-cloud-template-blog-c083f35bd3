"""Analyse specific parcels by number (e.g. ones you spotted on Zillow,
LandWatch, Facebook Marketplace, or a For Sale By Owner sign).

Text file: one parcel per line, optionally followed by a comma and a price:
    45321.9012, 89000
    P0512001002A
"""

from ..models import Listing
from .csv_import import parse_float


def load_parcel_ids(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            pid = parts[0]
            price = parse_float(parts[1]) if len(parts) > 1 else None
            out.append(Listing(source="parcel", id=f"parcel:{pid}", parcel_id=pid, price=price,
                               remarks=", ".join(parts[2:])))
    return out
