"""Build a small vector base map (counties, highways, water, towns) for the
hosted app, which can't load map tiles. Run once:

    python -m spokane_land.basemap > app/basemap.json
    python -m spokane_land.basemap --app app/basemap.json app/land_finder.html
"""

import json
import os
import sys

from . import arcgis
from .config import TIGER_GIS

BBOX = {"xmin": -118.02, "ymin": 47.26, "xmax": -116.82, "ymax": 48.07,
        "spatialReference": {"wkid": 4326}}
GEN = 0.0015  # degrees of server-side generalisation (~120 m)


def _q(url, where="1=1", fields="*", offset=GEN):
    params_geom = dict(BBOX)
    feats = arcgis.query(url, geometry=params_geom, where=where, out_fields=fields,
                         return_geometry=True)
    return feats


def _round(parts):
    return [[[round(x, 4), round(y, 4)] for x, y in part] for part in parts]


def _simplify(parts, tol=GEN):
    """Douglas-Peucker in degrees, enough for a regional overview."""
    def dp(pts):
        if len(pts) < 3:
            return pts
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        idx, dmax = 0, 0.0
        for i in range(1, len(pts) - 1):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / norm
            if d > dmax:
                idx, dmax = i, d
        if dmax <= tol:
            return [pts[0], pts[-1]]
        return dp(pts[: idx + 1])[:-1] + dp(pts[idx:])
    def one(p):
        p = [tuple(pt) for pt in p]
        if len(p) > 3 and p[0] == p[-1]:  # closed ring: split at the farthest vertex
            far = max(range(len(p)), key=lambda i: (p[i][0] - p[0][0]) ** 2 + (p[i][1] - p[0][1]) ** 2)
            return dp(p[: far + 1])[:-1] + dp(p[far:])
        return dp(p)
    out = [one(p) for p in parts]
    return [p for p in out if len(p) >= 2]


def _clip(parts, margin=0.3):
    keep = []
    for p in parts:
        if any(BBOX["xmin"] - margin < x < BBOX["xmax"] + margin and
               BBOX["ymin"] - margin < y < BBOX["ymax"] + margin for x, y in p):
            keep.append(p)
    return keep


def build():
    out = {"bbox": [BBOX["xmin"], BBOX["ymin"], BBOX["xmax"], BBOX["ymax"]]}

    counties = []
    for f in _q(f"{TIGER_GIS}/State_County/MapServer/1", "STATE IN ('53','16')", "NAME,STATE"):
        rings = _simplify(f["geometry"]["rings"], 0.002)
        counties.append({"name": f["attributes"]["NAME"].replace(" County", ""),
                         "rings": _round(_clip(rings))})
    out["counties"] = counties

    roads = []
    for layer, kind in ((2, "primary"), (3, "secondary")):
        for f in _q(f"{TIGER_GIS}/Transportation/MapServer/{layer}", "1=1", "NAME,RTTYP,MTFCC"):
            a = f["attributes"]
            if kind == "secondary" and a.get("MTFCC") != "S1200":
                continue
            paths = _simplify(f["geometry"]["paths"])
            roads.append({"name": a.get("NAME") or "", "kind": kind, "paths": _round(_clip(paths, 0))})
    out["roads"] = [r for r in roads if r["paths"]]

    water = []
    for f in _q(f"{TIGER_GIS}/Hydro/MapServer/1", "AREAWATER > 1500000 OR NAME LIKE 'Spokane Riv%'", "NAME,AREAWATER"):
        water.append({"name": f["attributes"].get("NAME") or "",
                      "rings": _round(_simplify(f["geometry"]["rings"], 0.0008))})
    out["water"] = [w for w in water if w["rings"]]

    rivers = []
    names = ("Spokane Riv", "Little Spokane Riv", "Hangman Cr", "Latah Cr")
    where = " OR ".join(f"NAME LIKE '{n}%'" for n in names)
    for f in _q(f"{TIGER_GIS}/Hydro/MapServer/0", where, "NAME"):
        rivers.append({"name": f["attributes"].get("NAME") or "",
                       "paths": _round(_simplify(f["geometry"]["paths"], 0.0008))})
    out["rivers"] = rivers

    towns = []
    for f in _q(f"{TIGER_GIS}/Places_CouSub_ConCity_SubMCD/MapServer/4", "1=1",
                "NAME,CENTLAT,CENTLON"):
        a = f["attributes"]
        rings = _simplify(f["geometry"]["rings"], 0.001)
        towns.append({"name": a["NAME"], "lat": float(a["CENTLAT"]), "lon": float(a["CENTLON"]),
                      "rings": _round(rings)})
    out["towns"] = towns
    return out


def build_app(basemap_path, out_path):
    """Embed the base map into the hosted app page."""
    here = os.path.join(os.path.dirname(__file__), "..", "app")
    with open(os.path.join(here, "app_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    with open(basemap_path, encoding="utf-8") as f:
        data = f.read().strip()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__BASEMAP__*/null", data))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--app":
        build_app(sys.argv[2], sys.argv[3])
    else:
        json.dump(build(), sys.stdout, separators=(",", ":"))
