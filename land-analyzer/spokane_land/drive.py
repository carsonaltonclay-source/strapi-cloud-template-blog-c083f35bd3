"""Drive time to downtown Spokane via the public OSRM routing server.

Uses the OSRM 'table' service in batches (one request per ~90 parcels), so a
full refresh of ~2,000 parcels is about two dozen requests.
"""

from .config import OSRM_URL, SPOKANE_LAT, SPOKANE_LON
from .http import HttpError, get_json

BATCH = 90


def drive_minutes(points):
    """points: list of (lat, lon). Returns list of minutes (or None) in the same order."""
    out = [None] * len(points)
    idx = [i for i, p in enumerate(points) if p[0] is not None and p[1] is not None]
    for start in range(0, len(idx), BATCH):
        chunk = idx[start:start + BATCH]
        coords = [f"{SPOKANE_LON:.5f},{SPOKANE_LAT:.5f}"] + [f"{points[i][1]:.5f},{points[i][0]:.5f}" for i in chunk]
        url = f"{OSRM_URL}/table/v1/driving/{';'.join(coords)}"
        params = {"sources": ";".join(str(k) for k in range(1, len(coords))), "destinations": "0", "annotations": "duration"}
        try:
            data = get_json(url, params)
        except HttpError:
            continue
        for k, row in zip(chunk, data.get("durations") or []):
            if row and row[0] is not None:
                out[k] = round(row[0] / 60)
    return out
