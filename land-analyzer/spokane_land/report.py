"""Write results as CSV, JSON and a self-contained interactive HTML report."""

import csv
import datetime
import html
import json
import os

CSV_FIELDS = [
    "score", "rating", "price", "acres", "price_per_acre", "miles_from_spokane",
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
