"""Write results as CSV, JSON and a self-contained interactive HTML report."""

import csv
import datetime
import glob
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
    "buildable", "buildable_acres", "deal_breakers", "to_check", "price_vs_sales", "sales_estimate", "assessed_value",
    "driveway_ft", "site_slope_pct", "cell_service", "school_district",
    "all_in_total", "drive_min", "soil_septic", "wildfire", "internet_mbps", "power_company", "zoning_check",
    "first_seen", "listed", "price_cut",
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
            row["all_in_total"] = (r.get("cost") or {}).get("total") or ""
            row["buildable"] = (r.get("buildable") or {}).get("label", "")
            site = r.get("site") or {}
            row["buildable_acres"] = site.get("buildable_acres", "")
            row["driveway_ft"] = (site.get("site") or {}).get("driveway_ft", "")
            row["site_slope_pct"] = (site.get("site") or {}).get("slope_pct", "")
            row["deal_breakers"] = " | ".join(c["label"] for c in r.get("checks") or [] if c["level"] == "bad")
            row["to_check"] = " | ".join(c["label"] for c in r.get("checks") or [] if c["level"] in ("warn", "info"))
            row["price_vs_sales"] = (r.get("price_check") or {}).get("label", "")
            row["sales_estimate"] = (r.get("comps") or {}).get("est_value") or ""
            row["assessed_value"] = (r.get("assessed") or {}).get("land_value") or ""
            row["cell_service"] = (r.get("cell") or {}).get("label", "")
            row["soil_septic"] = (r.get("soil") or {}).get("rating", "")
            row["wildfire"] = (r.get("wildfire") or {}).get("label", "")
            row["internet_mbps"] = (r.get("internet") or {}).get("down_mbps") or ""
            row["power_company"] = (r.get("power_company") or {}).get("name", "")
            row["zoning_check"] = (r.get("zoning_check") or {}).get("label", "")
            row["drive_min"] = r.get("drive_min") if r.get("drive_min") is not None else ""
            row["price_cut"] = r.get("price_cut") or ""
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


def _slim(r):
    """Drop what the app doesn't need (it rebuilds the cost breakdown and knows the sources)."""
    doc = dict(r, remarks=(r.get("remarks") or "")[:1500])
    for k in ("errors", "notes"):
        doc.pop(k, None)
    if doc.get("cost"):
        doc["cost"] = {k: doc["cost"][k] for k in ("total", "improvements", "inputs")}
    if doc.get("soil"):
        doc["soil"] = {k: v for k, v in doc["soil"].items() if k not in ("share", "worst", "source")}
        doc["soil"]["soils"] = doc["soil"].get("soils", [])[:1]
    # "Nothing found" checks only need their label in the app.
    doc["checks"] = [c if c["level"] != "ok" else {"key": c["key"], "level": "ok", "label": c["label"]}
                     for c in doc.get("checks") or []]
    if doc.get("rules"):  # offices and notes live once per county in meta.county_rules
        doc["rules"] = {k: v for k, v in doc["rules"].items() if k in ("county", "wria", "wria_note")}
    for k in ("wildfire", "internet", "power_company", "cell", "assessed"):
        if doc.get(k):
            doc[k] = {kk: v for kk, v in doc[k].items() if kk != "source" and v not in ("", None)}
    return doc


DOC_LIMIT = 240_000  # the app database refuses documents over 256 KiB


def pack(items, limit=DOC_LIMIT, max_items=None):
    """Split items into groups whose compact JSON stays under ``limit`` bytes."""
    groups, cur, size = [], [], 20
    for it in items:
        n = len(json.dumps(it, default=str, separators=(",", ":")).encode()) + 1
        if cur and (size + n > limit or (max_items and len(cur) >= max_items)):
            groups.append(cur)
            cur, size = [], 20
        cur.append(it)
        size += n
    if cur:
        groups.append(cur)
    return groups


def clear_dir(path, pattern="*.json"):
    os.makedirs(path, exist_ok=True)
    for f in glob.glob(os.path.join(path, pattern)):
        os.remove(f)


def export_db(results, out_dir, meta):
    """Database export for the hosted app.

    Bulk market listings go into collection "chunks" (as many listings per
    document as fit under the 256 KB document limit, so a refresh is a few
    batch uploads); properties added by hand go into "listings", one document
    each, keyed by their request id so they can be updated on their own.
    Also meta.json. Files from earlier exports are removed first.
    """
    for sub in ("chunks", "listings"):
        clear_dir(os.path.join(out_dir, sub))
    docs = [_slim(r) for r in results]
    bulk = [d for d in docs if d["source"] != "added"]
    for i, group in enumerate(pack(bulk)):
        with open(os.path.join(out_dir, "chunks", f"chunk-{i:03d}.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": group}, fh, default=str, separators=(",", ":"))
    for d in docs:
        if d["source"] == "added":
            with open(os.path.join(out_dir, "listings", doc_id(d["id"]) + ".json"), "w", encoding="utf-8") as fh:
                json.dump(d, fh, default=str)
    county_rules = {}
    for r in results:
        ru = r.get("rules") or {}
        if ru.get("county") and ru["county"] not in county_rules:
            county_rules[ru["county"]] = {"offices": ru.get("offices") or {}, "notes": ru.get("notes") or []}
    meta = dict(meta, county_rules=county_rules)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta, generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                       count=len(results)), fh)
    return len(results)
