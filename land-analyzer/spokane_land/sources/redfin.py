"""Redfin "Download All" CSV for vacant land inside the search circle.

Limitation: the regional MLSs that carry most Spokane-area land (Spokane
REALTORS MLS, Coeur d'Alene MLS) do not allow their listings in this download,
so Redfin returns only listings from other MLSs (e.g. NWMLS, PACMLS). Use the
RESO feed or a CSV export for complete coverage. This is the same file a user
gets from the download link on redfin.com; review Redfin's terms of use before
automating it.
"""

import csv
import sys
import io

from .. import geo
from ..config import REDFIN_CSV
from ..http import get_text
from ..models import Listing
from .csv_import import parse_float, parse_int


MAX_ROWS = 350  # Redfin's download limit


def fetch_redfin(lat, lon, radius_miles, use_cache=True):
    ring = geo.circle_polygon(lat, lon, radius_miles * geo.METERS_PER_MILE, segments=24)
    poly = ",".join(f"{x:.5f} {y:.5f}" for x, y in ring)
    listings = {}
    params = {
        "al": 1, "num_homes": MAX_ROWS, "ord": "price-asc", "page_number": 1,
        "status": 9, "uipt": 5,  # 9 = active + coming soon + under contract; 5 = land
        "v": 8, "user_poly": poly,
    }
    text = get_text(REDFIN_CSV, params, use_cache=use_cache)
    rows = [row for row in csv.DictReader(io.StringIO(text)) if row.get("ADDRESS") and row.get("LATITUDE")]
    if len(rows) >= MAX_ROWS:
        print(f"Warning: Redfin returned its {MAX_ROWS}-row maximum; listings priced above "
              f"${parse_float(rows[-1].get('PRICE')) or 0:,.0f} were cut off.", file=sys.stderr)
    for row in rows:
        url = row.get(next((k for k in row if k and k.startswith("URL")), ""), "")
        lot_sqft = parse_float(row.get("LOT SIZE"))
        item = Listing(
            source="redfin",
            id=f"redfin:{row.get('SOURCE', '')}:{row.get('MLS#', '')}",
            address=row.get("ADDRESS", ""),
            city=row.get("CITY", ""),
            state=row.get("STATE OR PROVINCE", ""),
            zip=row.get("ZIP OR POSTAL CODE", ""),
            price=parse_float(row.get("PRICE")),
            lot_acres=lot_sqft / 43560.0 if lot_sqft else None,
            lat=parse_float(row.get("LATITUDE")),
            lon=parse_float(row.get("LONGITUDE")),
            url=url,
            mls=f"{row.get('SOURCE', '')} {row.get('MLS#', '')}".strip(),
            status=row.get("STATUS", ""),
            days_on_market=parse_int(row.get("DAYS ON MARKET")),
        )
        listings[item.id] = item
    return list(listings.values())
