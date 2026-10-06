"""Write results as CSV, JSON and a self-contained interactive HTML report."""

import csv
import datetime
import html
import json
import os
import re

CSV_FIELDS = [
    "score", "rating", "price", "acres", "price_per_acre", "miles_from_spokane",
    "on_hill", "terrain", "slope_pct", "flat_pct", "elevation_ft",
    "water", "water_confidence", "electric", "electric_confidence",
    "septic", "septic_confidence", "access", "access_confidence",
    "address", "city", "state", "zip", "county", "parcel_id", "parcel_match", "zoning",
    "land_use", "days_on_market", "status", "mls", "source", "url", "lat", "lon",
    "flags", "water_detail", "electric_detail", "septic_detail", "access_detail",
]


def _detail(f):
    return " | ".join(f"{e['detail']} [{e['source']}]" for e in f["evidence"])


def write_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = dict(r)
            for cat in ("water", "electric", "septic", "access"):
                row[cat] = r[cat]["label"]
                row[f"{cat}_confidence"] = r[cat]["confidence"]
                row[f"{cat}_detail"] = _detail(r[cat])
            row["flags"] = " | ".join(r["flags"])
            t = r.get("terrain") or {}
            row["on_hill"] = {True: "yes", False: "no"}.get(t.get("on_hill"), "")
            row["terrain"] = f"{t['position_label']}; {t['slope_label']}" if t else ""
            row["slope_pct"] = t.get("slope_mean_pct", "")
            row["flat_pct"] = t.get("flat_pct", "")
            row["elevation_ft"] = t.get("elevation_ft", "")
            w.writerow(row)


def write_json(results, path, meta):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "results": results}, fh, indent=1, default=str)


def write_html(results, path, meta):
    tpl_path = os.path.join(os.path.dirname(__file__), "report_template.html")
    with open(tpl_path, encoding="utf-8") as fh:
        tpl = fh.read()
    data = json.dumps({"meta": meta, "results": results}, default=str).replace("</", "<\\/")
    out = tpl.replace("/*__DATA__*/null", data).replace(
        "__TITLE__", html.escape(meta.get("title", "Spokane Land Analyzer")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


def write_all(results, out_dir, meta):
    os.makedirs(out_dir, exist_ok=True)
    meta = dict(meta, generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    paths = {
        "csv": os.path.join(out_dir, "land_report.csv"),
        "json": os.path.join(out_dir, "land_report.json"),
        "html": os.path.join(out_dir, "land_report.html"),
    }
    write_csv(results, paths["csv"])
    write_json(results, paths["json"], meta)
    write_html(results, paths["html"], meta)
    return paths


def doc_id(listing_id):
    """Listing id -> database document id (letters, digits, _ - . ~ : @ +)."""
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", "_", listing_id)[:180]


def export_db(results, out_dir, meta, chunk_size=40):
    """Database export for the hosted app.

    Bulk market listings go into collection "chunks" (one document per
    ``chunk_size`` listings, so a full refresh is one batch upload);
    properties added by hand go into "listings", one document each, keyed by
    their request id so they can be updated on their own. Also meta.json.
    """
    for sub in ("chunks", "listings"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    docs = []
    for r in results:
        doc = dict(r, remarks=(r.get("remarks") or "")[:2000])
        doc.pop("errors", None)
        doc.pop("notes", None)
        docs.append(doc)
    bulk = [d for d in docs if d["source"] != "added"]
    for i in range(0, len(bulk), chunk_size):
        with open(os.path.join(out_dir, "chunks", f"chunk-{i // chunk_size:03d}.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": bulk[i:i + chunk_size]}, fh, default=str, separators=(",", ":"))
    for d in docs:
        if d["source"] == "added":
            with open(os.path.join(out_dir, "listings", doc_id(d["id"]) + ".json"), "w", encoding="utf-8") as fh:
                json.dump(d, fh, default=str)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta, generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                       count=len(results)), fh)
    return len(results)
