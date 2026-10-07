"""Listing photos for the app: each listing's main photo from the site it is listed on.

The app page can't load images from other websites, so each photo is downloaded once, shrunk to a
small WebP thumbnail and packed into bundle files that are published next to the page:

    <dir>/lp/index.json   {"credit": ..., "items": {doc id: [bundle, photo count, listing url]}}
    <dir>/lp/b-NNN.json   {doc id: "data:image/webp;base64,..."}

Bundles follow the app's default order (best score first), so the first screen of cards needs only
one or two of them. Publish the folder with the page, e.g. files={"lp/index.json": ".../lp/index.json", ...}.
Photos stay the property of the listing broker; the app shows them with a link back to the listing.

    python -m spokane_land.listing_photos output/land_report.json app_files
"""

import argparse
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import re
import sys
import threading
import urllib.request

from .config import USER_AGENT
from .http import CACHE_DIR
from .report import clear_dir, doc_id

THUMB_PX = 320
QUALITY = 45
PER_BUNDLE = 25


def _source_url(url):
    # Zillow's search thumbnails are 600 px; its 384 px size is half the download and plenty here.
    return re.sub(r"-p_e\.jpg$", "-cc_ft_384.jpg", url) if "zillowstatic.com" in url else url


def _fetch(url, cache_dir):
    path = os.path.join(cache_dir, hashlib.sha256(url.encode()).hexdigest()[:32] + ".img")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    for candidate in dict.fromkeys((_source_url(url), url)):
        for _ in range(2):
            try:
                req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
            except OSError:
                continue
            if data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG" or data[8:12] == b"WEBP":
                tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return data
            break  # an error page, not an image: try the other size
    return None


def _thumb(data):
    from PIL import Image  # optional dependency, only needed for photos
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((THUMB_PX, THUMB_PX))
    b = io.BytesIO()
    im.save(b, "WEBP", quality=QUALITY, method=6)
    return "data:image/webp;base64," + base64.b64encode(b.getvalue()).decode()


def build(results, out_dir, cache_dir=None, workers=8):
    """Write out_dir/lp/*. Returns the number of listings with a photo."""
    cache_dir = cache_dir or os.path.join(CACHE_DIR, "listing-photos")
    os.makedirs(cache_dir, exist_ok=True)
    lp = os.path.join(out_dir, "lp")
    clear_dir(lp)
    picked = [r for r in results if r.get("photo_url")]
    picked.sort(key=lambda r: (-(r.get("score") or 0), r.get("price") or 0, r["id"]))
    urls = list(dict.fromkeys(r["photo_url"] for r in picked))  # lots in one listing can share a photo
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        got = dict(zip(urls, pool.map(lambda u: _fetch(u, cache_dir), urls)))
    imgs = [got[r["photo_url"]] for r in picked]
    items, bundle, n_bundle = {}, {}, 0

    def flush():
        nonlocal bundle, n_bundle
        if bundle:
            with open(os.path.join(lp, f"b-{n_bundle:03d}.json"), "w", encoding="utf-8") as f:
                json.dump(bundle, f, separators=(",", ":"))
            bundle, n_bundle = {}, n_bundle + 1

    for i, (r, data) in enumerate(zip(picked, imgs)):
        if not data:
            continue
        try:
            uri = _thumb(data)
        except Exception:  # not a readable image
            continue
        doc = r.get("_doc") or doc_id(r["id"])
        bundle[doc] = uri
        items[doc] = [f"b-{n_bundle:03d}", r.get("photo_count") or 1, r.get("url") or ""]
        if len(bundle) >= PER_BUNDLE:
            flush()
        if i % 100 == 0:
            print(f"\r  listing photos {i + 1}/{len(picked)}", end="", flush=True, file=sys.stderr)
    flush()
    print(file=sys.stderr)
    with open(os.path.join(lp, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"credit": "Listing photos: the listing broker, via the listing site", "items": items},
                  f, separators=(",", ":"))
    return len(items)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("report", help="land_report.json")
    p.add_argument("out_dir", help="folder to write lp/ into (published next to the app page)")
    args = p.parse_args(argv)
    with open(args.report, encoding="utf-8") as f:
        results = json.load(f)["results"]
    n = build(results, args.out_dir)
    print(f"Listing photos: {n} of {len(results)} listings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
