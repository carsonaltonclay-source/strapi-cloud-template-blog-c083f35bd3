"""Command line entry point:  python -m spokane_land --help"""

import argparse
import concurrent.futures
import os
import sys

from . import comps, drive, geo, history
from .analyze import analyze
from .config import DEFAULT_RADIUS_MILES, SPOKANE_LAT, SPOKANE_LON
from .enrich import Enricher
from .report import export_db, write_all
from .sources import fetch_redfin, fetch_reso, load_csv, load_parcel_ids, load_requests


def build_parser():
    p = argparse.ArgumentParser(
        prog="spokane_land",
        description="Analyse land for sale near Spokane: price, water, power, septic and road access.")
    p.add_argument("--redfin", action="store_true",
                   help="pull vacant-land listings from Redfin's download (partial coverage, see README)")
    p.add_argument("--reso", action="store_true",
                   help="pull active land listings from an MLS RESO Web API (needs RESO_BASE_URL/RESO_TOKEN)")
    p.add_argument("--csv", action="append", default=[], metavar="FILE",
                   help="import listings from a CSV export (repeatable)")
    p.add_argument("--parcels", action="append", default=[], metavar="FILE",
                   help="text file of parcel numbers to analyse (repeatable)")
    p.add_argument("--requests", metavar="FILE",
                   help="JSON list of properties added in the hosted app ({id, text, price})")
    p.add_argument("--db-export", metavar="DIR",
                   help="also write one JSON document per listing for the hosted app's database")
    p.add_argument("--radius", type=float, default=DEFAULT_RADIUS_MILES,
                   help=f"search radius in miles from downtown Spokane (default {DEFAULT_RADIUS_MILES:g})")
    p.add_argument("--max-price", type=float, help="drop listings above this price")
    p.add_argument("--min-acres", type=float, help="drop listings smaller than this")
    p.add_argument("--out", default="output", help="output directory (default ./output)")
    p.add_argument("--workers", type=int, default=4, help="parallel GIS lookups (default 4)")
    p.add_argument("--previous", metavar="FILE",
                   help="last run's land_report.json: marks new listings, price cuts and removed listings")
    p.add_argument("--photos", type=float, metavar="MILES",
                   help="with --db-export: aerial photos for parcels within MILES of Spokane (needs Pillow)")
    p.add_argument("--no-drive", action="store_true", help="skip drive times (public OSRM routing server)")
    p.add_argument("--no-cache", action="store_true", help="ignore the 24h HTTP cache")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not (args.redfin or args.reso or args.csv or args.parcels or args.requests):
        build_parser().error("choose at least one source: --redfin, --reso, --csv FILE, --parcels FILE")

    listings, sources = [], []
    if args.redfin:
        got = fetch_redfin(SPOKANE_LAT, SPOKANE_LON, args.radius, use_cache=not args.no_cache)
        print(f"Redfin: {len(got)} land listings", file=sys.stderr)
        listings += got
        sources.append("Redfin download")
    if args.reso:
        got = fetch_reso(SPOKANE_LAT, SPOKANE_LON, args.radius)
        print(f"MLS (RESO): {len(got)} land listings", file=sys.stderr)
        listings += got
        sources.append("MLS RESO feed")
    for path in args.csv:
        got = load_csv(path)
        print(f"{path}: {len(got)} rows", file=sys.stderr)
        listings += got
        sources.append(f"CSV {path}")
    for path in args.parcels:
        got = load_parcel_ids(path)
        print(f"{path}: {len(got)} parcels", file=sys.stderr)
        listings += got
        sources.append(f"Parcels {path}")

    if args.requests:
        got = load_requests(args.requests)
        print(f"{args.requests}: {len(got)} added properties", file=sys.stderr)
        listings += got
        sources.append("Added by you")

    listings = _dedupe(listings)
    if args.max_price is not None:
        listings = [l for l in listings if l.price is None or l.price <= args.max_price]

    # Cheap pre-filter on what the listing already says (the exact check runs again after analysis).
    before = len(listings)
    listings = [l for l in listings if l.source == "added" or l.lat is None or l.lon is None
                or geo.miles_between(SPOKANE_LAT, SPOKANE_LON, l.lat, l.lon) <= args.radius + 0.5]
    if args.min_acres is not None:
        listings = [l for l in listings if l.source == "added" or l.lot_acres is None or l.lot_acres >= args.min_acres]
    if len(listings) < before:
        print(f"Skipped {before - len(listings)} listings outside the radius or under the minimum size", file=sys.stderr)

    enricher = Enricher(use_cache=not args.no_cache)
    results, shapes = [], {}

    def work(listing):
        facts = enricher.enrich(listing)
        p = facts.get("parcel")
        if p and p.geometry and p.geometry.get("rings"):
            shapes[listing.id] = p.geometry["rings"]
        return analyze(listing, facts)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, l): l for l in listings}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            l = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # keep going; one bad listing shouldn't stop the run
                print(f"  ! {l.id}: {e}", file=sys.stderr)
            print(f"\r  analysed {i}/{len(listings)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    kept = []
    for r in results:
        # Properties someone added by hand are always kept.
        if r["source"] != "added" and r["miles_from_spokane"] is not None and r["miles_from_spokane"] > args.radius:
            continue
        if args.min_acres is not None and r["acres"] is not None and r["acres"] < args.min_acres:
            continue
        kept.append(r)
    before = len(kept)
    kept = history.dedupe(kept)
    if len(kept) < before:
        print(f"Merged {before - len(kept)} duplicate listings of the same parcel", file=sys.stderr)
    kept.sort(key=lambda r: (-r["score"], r["price"] or 0))

    if not args.no_drive:
        minutes = drive.drive_minutes([(r["lat"], r["lon"]) for r in kept])
        for r, m in zip(kept, minutes):
            r["drive_min"] = m
        print(f"Drive times: {sum(m is not None for m in minutes)}/{len(kept)}", file=sys.stderr)

    previous = history.load_previous(args.previous) if args.previous else []
    refresh = history.apply(kept, previous)
    for r in kept:
        r["price_check"] = comps.price_verdict(r.get("price"), r.get("comps"), r.get("assessed"),
                                               r.get("days_on_market"), r.get("price_cut"))
    if previous:
        print(f"Since last refresh: {refresh['new']} new, {refresh['price_cuts']} price cuts, "
              f"{refresh['removed']} gone", file=sys.stderr)

    meta = {
        "title": f"Land within {args.radius:g} miles of Spokane",
        "center": [SPOKANE_LAT, SPOKANE_LON],
        "radius_miles": args.radius,
        "sources": sources,
        "refresh": refresh,
    }
    if args.db_export and args.photos is not None:
        from . import photos
        cache = os.path.join(os.path.dirname(args.db_export.rstrip("/")) or ".", "photo-cache")
        n = photos.build(kept, shapes, args.db_export, cache,
                         select=lambda r: (r["miles_from_spokane"] or 0) <= args.photos or r["source"] == "added")
        print(f"Aerial photos: {n}", file=sys.stderr)
    paths = write_all(kept, args.out, meta)
    if args.db_export:
        n = export_db(kept, args.db_export, meta)
        print(f"Database export: {n} documents in {args.db_export}", file=sys.stderr)
    _print_summary(kept)
    print(f"\nWrote {paths['html']}\n      {paths['csv']}\n      {paths['json']}")
    return 0


def _dedupe(listings):
    """Same land from several sources: keep the one with the most detail."""
    best = {}
    for l in listings:
        if l.lat is not None and l.lon is not None:
            key = (round(l.lat, 4), round(l.lon, 4), round(l.price or 0, -2))
        elif l.parcel_id:
            key = ("pid", l.parcel_id)
        else:
            key = ("id", l.id)
        richness = len(l.remarks) + 200 * bool(l.water_source or l.electric or l.sewer)
        if key not in best or richness > best[key][0]:
            best[key] = (richness, l)
    return [v[1] for v in best.values()]


def _print_summary(results):
    print(f"\n{len(results)} listings\n")
    hdr = f"{'Score':>5}  {'Price':>10}  {'Acres':>6}  {'Water':<24} {'Power':<24} {'Septic':<24} {'Access':<22} Address"
    print(hdr)
    print("-" * len(hdr))
    for r in results[:60]:
        price = f"${r['price']:,.0f}" if r["price"] else "?"
        acres = f"{r['acres']:.2f}" if r["acres"] else "?"
        print(f"{r['score']:>5}  {price:>10}  {acres:>6}  {r['water']['label'][:23]:<24} "
              f"{r['electric']['label'][:23]:<24} {r['septic']['label'][:23]:<24} "
              f"{r['access']['label'][:21]:<22} {r['address']}, {r['city']}")
    if len(results) > 60:
        print(f"... {len(results) - 60} more in the report")


if __name__ == "__main__":
    sys.exit(main())
