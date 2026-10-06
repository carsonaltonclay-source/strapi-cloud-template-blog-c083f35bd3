"""Data types shared across sources, enrichment and reporting."""

from dataclasses import dataclass, field, asdict
from typing import Optional

HIGH, MEDIUM, LOW = "high", "medium", "low"
_CONF_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}


@dataclass
class Listing:
    source: str
    id: str
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    price: Optional[float] = None
    lot_acres: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    url: str = ""
    mls: str = ""
    status: str = ""
    days_on_market: Optional[int] = None
    parcel_id: str = ""
    remarks: str = ""
    # Structured MLS fields (RESO names) when the source provides them.
    water_source: str = ""
    electric: str = ""
    sewer: str = ""
    road_access: str = ""


@dataclass
class Evidence:
    status: str          # canonical status key, see STATUS_LABELS in analyze.py
    confidence: str      # high / medium / low
    detail: str          # human readable explanation
    source: str          # where it came from


@dataclass
class Finding:
    """Final verdict for one category (water, electric, septic, access)."""
    status: str = "unknown"
    label: str = "Unknown"
    confidence: str = LOW
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "label": self.label,
            "confidence": self.confidence,
            "evidence": [asdict(e) for e in self.evidence],
        }


@dataclass
class Parcel:
    parcel_id: str = ""
    county: str = ""
    state: str = ""
    acres: Optional[float] = None
    owner: str = ""
    land_use: str = ""
    site_address: str = ""
    geometry: Optional[dict] = None   # Esri polygon in WGS84
    match: str = ""                   # how the listing was matched to the parcel


def conf_rank(c):
    return _CONF_RANK.get(c, 0)
