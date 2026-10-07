"""Refresh bookkeeping: duplicates, new listings, price changes, removed listings.

Pass the previous run's land_report.json with --previous and each listing gets:
  first_seen     date this tool first saw it (or listing date from days on market)
  price_history  [{date, price}] whenever the asking price changed
  is_new         first seen in this refresh (not on the very first run)
  price_cut      dollars the price dropped since the previous refresh, if any
"""

import datetime
import glob
import json
import os
import re

from .report import doc_id


def _key(r):
    return doc_id(r["id"])


def _lot_no(r):
    m = re.search(r"\blot\s*#?\s*([\w-]+)", r.get("address") or "", re.I)
    return m.group(1).upper() if m else None


def _richness(r):
    return len(r.get("remarks") or "") + (50 if r.get("url") else 0) + (20 if r.get("address") else 0)


def dedupe(results):
    """Same parcel listed twice at the same price and size (e.g. '21 S Harrison
    Rd Lot 1' and '21 S Harrison Rd'): keep the one with more information.
    Lots with different lot numbers are never merged."""
    keep, idx, lots = [], {}, {}
    for r in results:
        pid = r.get("parcel_id")
        if not pid or r.get("price") is None:
            keep.append(r)
            continue
        base = (pid, round(r["price"] / 1000), round(r.get("acres") or 0))
        lot = _lot_no(r)
        # A group may hold one numbered lot (plus unnumbered copies of it);
        # a different lot number starts its own group.
        k = next((g for g in idx if g[:3] == base and (not lot or not lots[g] or lots[g] == lot)), None)
        if k is not None:
            cur = keep[idx[k]]
            # Ties go to the higher id, so the same copy wins on every run (marks are keyed by it).
            win, lose = (r, cur) if (_richness(r), r["id"]) > (_richness(cur), cur["id"]) else (cur, r)
            win["also_listed_as"] = ((win.get("also_listed_as") or []) + [lose.get("address") or lose["id"]]
                                     + (lose.get("also_listed_as") or []))
            keep[idx[k]] = win
            lots[k] = lots[k] or lot
            continue
        k = base + (lot,)
        idx[k], lots[k] = len(keep), lot
        keep.append(r)
    return keep


def apply(results, previous, today=None):
    today = today or datetime.date.today().isoformat()
    prev = {_key(r): r for r in (previous or [])}
    first_run = not prev
    for r in results:
        p = prev.get(_key(r))
        dom = r.get("days_on_market")
        dom = dom if dom is not None and dom >= 0 else None
        # Listing date (for "listed N days ago"); first_seen is when this tool first saw it.
        r["listed"] = ((datetime.date.fromisoformat(today) - datetime.timedelta(days=dom)).isoformat() if dom is not None
                       else (p or {}).get("listed"))
        if p:
            r["first_seen"] = p.get("first_seen") or today
            hist = list(p.get("price_history") or [])
            if not hist and p.get("price") is not None:
                hist = [{"date": p.get("first_seen") or today, "price": p["price"]}]
            if r.get("price") is not None and hist and hist[-1]["price"] != r["price"]:
                hist.append({"date": today, "price": r["price"]})
                if r["price"] < p.get("price", r["price"]):
                    r["price_cut"] = round(p["price"] - r["price"])
            r["price_history"] = hist
            r["is_new"] = False
        else:
            r["first_seen"] = (r["listed"] or today) if first_run else today
            r["price_history"] = [{"date": r["first_seen"], "price": r["price"]}] if r.get("price") is not None else []
            r["is_new"] = not first_run
    current = {_key(r) for r in results}
    removed = [{"id": p["id"], "address": p.get("address"), "city": p.get("city"), "price": p.get("price")}
               for k, p in prev.items() if k not in current]
    return {
        "date": today,
        "new": sum(1 for r in results if r.get("is_new")),
        "price_cuts": sum(1 for r in results if r.get("price_cut")),
        "removed": len(removed),
        "removed_list": removed[:200],
        "first_run": first_run,
    }


def load_previous(path):
    """A land_report.json, or a folder of the app's chunk documents (as saved by
    ArtifactData list with out_dir: <dir>/chunks/chunk-*.json or <dir>/chunk-*.json)."""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "chunk-*.json")) + glob.glob(os.path.join(path, "chunks", "chunk-*.json")))
        out = []
        for fp in files:
            with open(fp, encoding="utf-8") as f:
                out.extend(json.load(f).get("items", []))
        for fp in sorted(glob.glob(os.path.join(path, "listings", "*.json"))):  # hand-added properties
            with open(fp, encoding="utf-8") as f:
                doc = json.load(f)
            out.append(doc.get("data", doc))
        return out
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("results", [])
