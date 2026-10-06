"""Aerial photo of each parcel for the app (USDA NAIP via USGS The National Map,
public domain), with the parcel outline and best house site as overlay paths.

Photos are small WebP images stored in the app database, ~20 per document
("photos" collection), so a whole refresh is a handful of uploads. Each listing
gets r["photo"] = {"doc": "p-012", "path": "M..Z", "site": [x, y], "credit": ...}
with coordinates in 0-100 image units.
"""

import base64
import io
import json
import math
import os
import urllib.request

from .config import USER_AGENT

EXPORT = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export"
SIZE = 320
PER_DOC = 20
CREDIT = "USDA NAIP / USGS"


def _merc(lon, lat):
    return lon * 20037508.34 / 180, math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137


def _frame(rings):
    pts = [_merc(x, y) for r in rings for x, y in r]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys), 120) * 0.62  # parcel plus a margin, at least ~150 m across
    return cx - half, cy - half, cx + half, cy + half


def _fetch(bbox):
    params = {"bbox": ",".join(f"{v:.1f}" for v in bbox), "bboxSR": 3857, "imageSR": 3857,
              "size": f"{SIZE},{SIZE}", "format": "jpg", "f": "image"}
    url = EXPORT + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if data[:2] == b"\xff\xd8":
                return data
        except OSError:
            continue
    return None


def _webp_data_uri(jpg):
    from PIL import Image  # optional dependency, only needed for photos
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    b = io.BytesIO()
    im.save(b, "WEBP", quality=55, method=6)
    return "data:image/webp;base64," + base64.b64encode(b.getvalue()).decode()


def _overlay(rings, bbox, site):
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0

    def uv(lon, lat):
        x, y = _merc(lon, lat)
        return (x - x0) / w * 100, (y1 - y) / h * 100

    parts = []
    for ring in rings:
        step = max(1, len(ring) // 60)
        pts = [uv(x, y) for x, y in ring[::step]]
        parts.append("M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + "Z")
    s = uv(site["lon"], site["lat"]) if site else None
    return "".join(parts), ([round(s[0], 1), round(s[1], 1)] if s else None)


def build(results, shapes, out_dir, cache_dir, select=None):
    """results: analysed listings; shapes: {listing id: parcel rings}. Writes out_dir/photos/p-NNN.json
    and sets r["photo"]. Returns the number of photos."""
    os.makedirs(os.path.join(out_dir, "photos"), exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    picked = [r for r in results if r["id"] in shapes and (select is None or select(r))]
    docs, n = {}, 0
    for i, r in enumerate(picked):
        rings = shapes[r["id"]]
        bbox = _frame(rings)
        key = "_".join(f"{v:.0f}" for v in bbox)
        cache = os.path.join(cache_dir, key + ".jpg")
        if os.path.exists(cache):
            with open(cache, "rb") as f:
                jpg = f.read()
        else:
            jpg = _fetch(bbox)
            if jpg:
                with open(cache, "wb") as f:
                    f.write(jpg)
        if not jpg:
            continue
        doc = f"p-{n // PER_DOC:03d}"
        docs.setdefault(doc, {})[r["_doc"] if "_doc" in r else _doc_id(r["id"])] = _webp_data_uri(jpg)
        path, site = _overlay(rings, bbox, (r.get("site") or {}).get("site"))
        r["photo"] = {"doc": doc, "path": path, "site": site, "credit": CREDIT}
        n += 1
        if i % 50 == 0:
            print(f"\r  photos {i + 1}/{len(picked)}", end="", flush=True)
    print()
    for doc, imgs in docs.items():
        with open(os.path.join(out_dir, "photos", doc + ".json"), "w", encoding="utf-8") as f:
            json.dump({"imgs": imgs}, f, separators=(",", ":"))
    return n


def _doc_id(listing_id):
    from .report import doc_id
    return doc_id(listing_id)
