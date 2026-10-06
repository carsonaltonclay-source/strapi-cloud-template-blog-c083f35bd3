"""Saved-search alerts: which new or price-cut listings match each saved search.

Saved searches are made in the hosted app (database collection "searches",
one document per search: {name, notify, state}). ``state`` holds the app's
filters; ``matches`` below mirrors the app's ``passes()`` (app/app_template.html)
— keep the two in sync. The monthly-budget filter depends on each viewer's own
loan assumptions, so alerts ignore it (as they ignore "Starred" and tracking status).

    python -m spokane_land.alerts searches.json output/land_report.json --out alert

writes alert.html and alert.txt (an email body) when anything matched, and
prints a one-line summary. searches.json is a list of saved-search documents.
"""

import argparse
import html
import json
import sys

WATER_OK = {"well", "public", "shared_well", "public_area"}
POWER_OK = {"on_site", "at_road", "likely_near"}
SOIL_OK = {"good", "design"}


def _status(r, cat):
    if cat == "_terrain":
        return (r.get("terrain") or {}).get("slope_class") or "unknown"
    return (r.get(cat) or {}).get("status")


def matches(r, st):
    f = st.get("f") or {}
    q = (st.get("q") or "").strip().lower()
    if q and q not in " ".join(str(r.get(k) or "") for k in ("address", "city", "parcel_id", "zip", "county", "mls")).lower():
        return False
    price = r.get("price")
    if price is not None and (price < (st.get("min_price") or 0)
                              or (st.get("max_price") is not None and price > st["max_price"])):
        return False
    if st.get("min_acres") and r.get("acres") is not None and r["acres"] < st["min_acres"]:
        return False
    if f.get("water") and _status(r, "water") not in WATER_OK:
        return False
    if f.get("power") and _status(r, "electric") not in POWER_OK:
        return False
    if f.get("access") and _status(r, "access") == "landlocked":
        return False
    if f.get("soil") and not (_status(r, "septic") in ("sewer", "installed", "approved")
                              or (r.get("soil") or {}).get("outlook") in SOIL_OK):
        return False
    if f.get("clean") and any(c.get("level") == "bad" for c in r.get("checks") or []):
        return False
    if f.get("fire"):
        c = (r.get("wildfire") or {}).get("class")
        if c is None or 2 < c < 6:
            return False
    g = st.get("glance")
    if g and _status(r, g["cat"]) not in g.get("statuses", []):
        return False
    if r.get("rating") in (st.get("hidden") or []):
        return False
    radius = st.get("radius")
    if radius and r.get("miles_from_spokane") is not None and r["miles_from_spokane"] > radius and r.get("source") != "added":
        return False
    tf, t = st.get("terrain"), r.get("terrain")
    if tf:
        if not t:
            return False
        if ((tf == "hill" and not t.get("on_hill")) or (tf in ("hilltop", "hillside", "valley") and t.get("position") != tf)
                or (tf == "flat" and t.get("slope_class") not in ("flat", "gentle"))
                or (tf == "notsteep" and t.get("slope_class") in ("steep", "very_steep"))):
            return False
    return True


def collect(searches, results):
    """[(search, [listing, ...])] for searches with notify on and at least one new/price-cut match."""
    fresh = [r for r in results if r.get("is_new") or r.get("price_cut")]
    out = []
    for s in searches:
        if not s.get("notify", True):
            continue
        hits = [r for r in fresh if matches(r, s.get("state") or {})]
        if hits:
            hits.sort(key=lambda r: (not r.get("is_new"), -(r.get("score") or 0)))
            out.append((s, hits))
    return out


def _money(v):
    return "—" if v is None else f"${v:,.0f}"


def _line(r):
    what = "NEW" if r.get("is_new") else f"PRICE CUT −{_money(r.get('price_cut'))}"
    loc = ", ".join(x for x in (r.get("address"), r.get("city")) if x) or f"Parcel {r.get('parcel_id')}"
    allin = (r.get("cost") or {}).get("total")
    return (what, loc, f"{_money(r.get('price'))} · {r.get('acres') or '?'} ac · score {r.get('score')}"
            + (f" · ≈{_money(allin)} all-in" if allin else "")
            + f" · water: {r['water']['label']}; power: {r['electric']['label']}", r.get("url") or "")


def render(groups, app_url):
    total = sum(len(h) for _, h in groups)
    subject = f"Spokane Land Finder: {total} new match{'es' if total != 1 else ''} for your saved searches"
    txt, parts = [subject, ""], []
    for s, hits in groups:
        txt.append(f"== {s['name']} ({len(hits)}) ==")
        rows = []
        for r in hits[:25]:
            what, loc, info, url = _line(r)
            txt.append(f"- {what}: {loc}\n  {info}" + (f"\n  {url}" if url else ""))
            rows.append(f'<li><b>{html.escape(what)}</b> · {html.escape(loc)}<br><span style="color:#555">{html.escape(info)}</span>'
                        + (f' · <a href="{html.escape(url)}">listing</a>' if url else "") + "</li>")
        if len(hits) > 25:
            txt.append(f"  …and {len(hits) - 25} more in the app")
        txt.append("")
        parts.append(f"<h3 style=\"margin:18px 0 6px\">{html.escape(s['name'])} ({len(hits)})</h3><ul>{''.join(rows)}</ul>")
    txt.append(f"Open the app: {app_url}")
    body_html = (f"<div style=\"font-family:system-ui,sans-serif;font-size:14px\"><h2>{html.escape(subject)}</h2>{''.join(parts)}"
                 f"<p><a href=\"{html.escape(app_url)}\">Open Spokane Land Finder</a></p></div>")
    return subject, "\n".join(txt), body_html


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("searches", help="JSON list of saved-search documents")
    p.add_argument("report", help="land_report.json from a refresh run with --previous")
    p.add_argument("--out", default="alert", help="output path prefix (writes .txt and .html)")
    p.add_argument("--app-url", default="https://claude.ai/artifact/Phe43Pu3ykK6Cnz3hVGuQD")
    args = p.parse_args(argv)
    with open(args.searches, encoding="utf-8") as f:
        searches = json.load(f)
    with open(args.report, encoding="utf-8") as f:
        results = json.load(f)["results"]
    groups = collect(searches, results)
    if not groups:
        print("No new matches for any saved search.")
        return 0
    subject, txt, body = render(groups, args.app_url)
    with open(args.out + ".txt", "w", encoding="utf-8") as f:
        f.write(txt)
    with open(args.out + ".html", "w", encoding="utf-8") as f:
        f.write(body)
    print(subject, file=sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
