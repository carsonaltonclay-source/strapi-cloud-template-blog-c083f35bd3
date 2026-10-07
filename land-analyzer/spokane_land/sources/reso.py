"""RESO Web API (OData) feed — the complete source for MLS land listings.

Spokane REALTORS MLS, NWMLS and Coeur d'Alene MLS all expose RESO Web API
feeds to members / IDX vendors (often via Spark Platform, Bridge, Trestle or
MLS Grid). A licensed agent or broker can request IDX/VOW credentials. Set:

    RESO_BASE_URL   e.g. https://replication.sparkapi.com/Reso/OData
    RESO_TOKEN      bearer access token

The RESO Data Dictionary already has fields for every attribute this tool
analyses (WaterSource, Electric, Sewer, RoadFrontageType...), so these
listings get the highest-confidence results.
"""

import os
import urllib.parse

from .. import geo
from ..http import get_json
from ..models import Listing

SELECT = [
    "ListingKey", "ListingId", "ListPrice", "LotSizeAcres", "LotSizeSquareFeet",
    "Latitude", "Longitude", "UnparsedAddress", "City", "StateOrProvince", "PostalCode",
    "ParcelNumber", "PublicRemarks", "WaterSource", "Electric", "Utilities", "Sewer",
    "RoadFrontageType", "RoadSurfaceType", "RoadResponsibility", "DaysOnMarket",
    "StandardStatus", "PropertyType",
]


def _join(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x)
    return str(v or "")


def fetch_reso(lat, lon, radius_miles, base_url=None, token=None, max_pages=50):
    base_url = (base_url or os.environ.get("RESO_BASE_URL", "")).rstrip("/")
    token = token or os.environ.get("RESO_TOKEN", "")
    if not base_url or not token:
        raise SystemExit("RESO source needs RESO_BASE_URL and RESO_TOKEN (see README).")

    min_lon, min_lat, max_lon, max_lat = geo.bbox_of_circle(lat, lon, radius_miles * geo.METERS_PER_MILE)
    flt = (
        "PropertyType eq 'Land' and StandardStatus eq 'Active'"
        f" and Latitude ge {min_lat:.5f} and Latitude le {max_lat:.5f}"
        f" and Longitude ge {min_lon:.5f} and Longitude le {max_lon:.5f}"
    )
    url = f"{base_url}/Property?" + urllib.parse.urlencode(
        {"$filter": flt, "$select": ",".join(SELECT), "$top": 200}
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out = []
    for _ in range(max_pages):
        data = get_json(url, headers=headers, use_cache=False)
        for r in data.get("value", []):
            acres = r.get("LotSizeAcres")
            if not acres and r.get("LotSizeSquareFeet"):
                acres = r["LotSizeSquareFeet"] / 43560.0
            out.append(Listing(
                source="mls",
                id=f"mls:{r.get('ListingKey')}",
                address=r.get("UnparsedAddress") or "",
                city=r.get("City") or "",
                state=r.get("StateOrProvince") or "",
                zip=r.get("PostalCode") or "",
                price=r.get("ListPrice"),
                lot_acres=acres,
                lat=r.get("Latitude"),
                lon=r.get("Longitude"),
                mls=str(r.get("ListingId") or ""),
                status=r.get("StandardStatus") or "",
                days_on_market=r.get("DaysOnMarket"),
                parcel_id=r.get("ParcelNumber") or "",
                remarks=r.get("PublicRemarks") or "",
                water_source=_join(r.get("WaterSource")),
                electric=_join(r.get("Electric")) or _join(r.get("Utilities")),
                sewer=_join(r.get("Sewer")),
                road_access=", ".join(x for x in (_join(r.get("RoadFrontageType")),
                                                  _join(r.get("RoadSurfaceType"))) if x),
            ))
        url = data.get("@odata.nextLink")
        if not url:
            break
    return out
