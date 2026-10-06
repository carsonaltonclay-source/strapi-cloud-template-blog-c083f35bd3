"""Look up public GIS facts about a listing's parcel.

Everything here is best-effort: a failed service call is recorded in
``facts["errors"]`` and analysis continues with whatever was found.
"""

import datetime
import re
import statistics

from . import arcgis, buildability, geo
from .config import (
    CENSUS_GEOCODER, LAYERS, NEIGHBOR_SEARCH_RADIUS_M, POINT_PARCEL_SNAP_M,
    ROAD_FRONTAGE_TOLERANCE_M, ROAD_SEARCH_RADIUS_M, WELL_SEARCH_RADIUS_M,
)
from .http import HttpError, get_json
from .models import Parcel
from .terrain import analyze_terrain

# WA/ID border north of the Clearwater River runs along this meridian.
WA_ID_BORDER_LON = -117.0399

ID_WATER_WELL_USES = ("domestic", "irrigation", "stockwater", "multiple", "public water",
                      "municipal", "commercial")
TIGER_PUBLIC = {"S1100": "Interstate/US highway", "S1200": "State/county highway",
                "S1400": "Local road", "S1640": "Service road"}
TIGER_PRIVATE = {"S1740": "Private road", "S1500": "4WD trail", "S1730": "Alley",
                 "S1780": "Parking lot road"}
SC_PRIVATE_CLASSES = {"Easement", "Unimproved", "Public Safety Only", "Unknown", "Other"}


def norm_pid(s):
    return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper()


class Enricher:
    def __init__(self, use_cache=True):
        self.use_cache = use_cache

    # ---- helpers --------------------------------------------------------------

    def _q(self, facts, name, **kw):
        try:
            return arcgis.query(LAYERS[name], **kw)
        except HttpError as e:
            facts["errors"].append(f"{name}: {e}")
            return []

    # ---- location & parcel -----------------------------------------------------

    def geocode(self, listing, facts):
        addr = ", ".join(x for x in (listing.address, listing.city, listing.state, listing.zip) if x)
        if not addr or re.match(r"^\s*(nka|tbd|xxx|lot|parcel|0+)\b", listing.address or "", re.I):
            return
        try:
            data = get_json(CENSUS_GEOCODER, {"address": addr, "benchmark": "Public_AR_Current",
                                              "format": "json"})
        except HttpError as e:
            facts["errors"].append(f"geocode: {e}")
            return
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            listing.lon, listing.lat = c["x"], c["y"]
            facts["notes"].append("Location from US Census geocoder (address-range estimate).")

    def find_parcel(self, listing, facts):
        """Return a Parcel or None. Tries parcel number first, then the map pin."""
        if listing.parcel_id:
            p = self._parcel_by_id(listing.parcel_id, facts)
            if p:
                return p
        if listing.lat is None or listing.lon is None:
            return None
        pt = (listing.lon, listing.lat)
        state = "ID" if listing.lon > WA_ID_BORDER_LON else "WA"

        if state == "WA":
            p = self._first_parcel("sc_parcels", pt, facts, self._sc_parcel)
            if p:
                return p
            p = self._first_parcel("wa_parcels", pt, facts, self._wa_parcel)
            if p:
                return p
        else:
            p = self._first_parcel("id_parcels", pt, facts, self._id_parcel)
            if p:
                return p
        return None

    def _first_parcel(self, layer, pt, facts, build):
        feats = self._q(facts, layer, geometry=pt, return_geometry=True)
        match = "map pin inside parcel"
        if not feats:
            # Listing pins for vacant land are often dropped on the road.
            feats = self._q(facts, layer, geometry=pt, distance_m=POINT_PARCEL_SNAP_M,
                            return_geometry=True, max_records=20)
            if feats:
                feats.sort(key=lambda f: _dist_point_polygon(pt, f.get("geometry")))
                match = f"nearest parcel to map pin (within {POINT_PARCEL_SNAP_M} m) — verify"
        if not feats:
            return None
        p = build(feats[0])
        p.match = match
        return p

    def _parcel_by_id(self, pid, facts):
        raw = pid.strip().replace("'", "")
        tries = [
            ("sc_parcels", f"PID_NUM = '{raw}'", self._sc_parcel),
            ("wa_parcels", f"PARCEL_ID_NR = '{raw}' OR ORIG_PARCEL_ID = '{raw}'", self._wa_parcel),
            ("id_parcels", f"PIN = '{raw}'", self._id_parcel),
        ]
        for layer, where, build in tries:
            feats = self._q(facts, layer, where=where, return_geometry=True, max_records=1)
            if feats:
                p = build(feats[0])
                p.match = "parcel number"
                return p
        return None

    def _sc_parcel(self, f):
        a = f["attributes"]
        return Parcel(parcel_id=a.get("PID_NUM") or "", county="Spokane", state="WA",
                      acres=a.get("acreage"), site_address=a.get("site_address") or "",
                      geometry=f.get("geometry"))

    def _wa_parcel(self, f):
        a = f["attributes"]
        return Parcel(parcel_id=a.get("PARCEL_ID_NR") or a.get("ORIG_PARCEL_ID") or "",
                      county=(a.get("COUNTY_NM") or "").title(), state="WA",
                      site_address=a.get("SITUS_ADDRESS") or "", geometry=f.get("geometry"))

    def _id_parcel(self, f):
        a = f["attributes"]
        return Parcel(parcel_id=a.get("PIN") or "", county=(a.get("COUNTY") or "").title(),
                      state="ID", owner=a.get("OWNER") or "", geometry=f.get("geometry"))

    def spokane_property(self, parcel, facts):
        feats = self._q(facts, "sc_property", where=f"PID_NUM = '{parcel.parcel_id}'",
                        out_fields="owner_name,prop_use_desc,acreage,site_address,gross_sale_price,document_date",
                        max_records=1)
        if feats:
            a = feats[0]["attributes"]
            parcel.owner = a.get("owner_name") or ""
            parcel.land_use = a.get("prop_use_desc") or ""
            parcel.acres = parcel.acres or a.get("acreage")
            parcel.site_address = parcel.site_address or a.get("site_address") or ""

    # ---- main entry ------------------------------------------------------------

    def enrich(self, listing):
        facts = {"errors": [], "notes": [], "parcel": None}
        if (listing.lat is None or listing.lon is None) and not listing.parcel_id:
            self.geocode(listing, facts)

        parcel = self.find_parcel(listing, facts)
        if parcel and parcel.county == "Spokane":
            self.spokane_property(parcel, facts)
        if parcel and parcel.geometry and parcel.geometry.get("rings"):
            parcel.geometry = arcgis.simplify_polygon(
                {"rings": parcel.geometry["rings"], "spatialReference": {"wkid": 4326}})
            if parcel.acres is None:
                parcel.acres = round(geo.polygon_area_acres(parcel.geometry["rings"]), 2)
            if listing.lat is None or listing.lon is None:
                listing.lat, listing.lon = geo.polygon_centroid(parcel.geometry["rings"])
        facts["parcel"] = parcel

        if listing.lat is None or listing.lon is None:
            facts["notes"].append("No coordinates or parcel match — only listing text was analysed.")
            return facts

        state = parcel.state if parcel else ("ID" if listing.lon > WA_ID_BORDER_LON else "WA")
        facts["state"] = state
        in_spokane = bool(parcel and parcel.county == "Spokane")
        shape = parcel.geometry if parcel and parcel.geometry else None
        target = shape or (listing.lon, listing.lat)

        facts["wells"] = self.wells(listing, parcel, state, facts)
        if in_spokane or (state == "WA" and not parcel):
            facts["water_district"] = self._names(self._q(
                facts, "sc_water_districts", geometry=target, out_fields="NAME,SYSTEMTYPE"), "NAME")
            facts["city"] = self._names(self._q(facts, "sc_municipal", geometry=target, out_fields="*"),
                                        ("NAME", "Name", "MUNI_NAME", "CITY"))
            facts["uga"] = self._names(self._q(facts, "sc_uga", geometry=target, out_fields="*"),
                                       ("NAME", "Name", "UGA_NAME", "UGA"))
            facts["aquifer"] = bool(self._q(facts, "sc_aquifer", geometry=target, out_fields="OBJECTID"))
            facts["flood"] = self._names(self._q(facts, "sc_flood", geometry=target, where="SFHA_TF = 'T'",
                                                 out_fields="FLD_ZONE"), "FLD_ZONE")
            facts["zoning"] = self._names(self._q(facts, "sc_zoning", geometry=target,
                                                  out_fields="ZONECLASS,ZONEDESC"), "ZONEDESC")
            facts["neighbors"] = self.address_points(listing, parcel, facts)
        elif state == "ID":
            facts["water_district"] = self._names(self._q(
                facts, "id_water_areas", geometry=target, out_fields="Name,Owner"), "Name")

        facts["roads"] = self.roads(listing, parcel, state, facts)
        facts["terrain"] = analyze_terrain(parcel.geometry if parcel else None, listing.lat, listing.lon,
                                           parcel.acres if parcel and parcel.acres else listing.lot_acres)
        if facts["terrain"] is None:
            facts["errors"].append("terrain: elevation service gave no answer")
        for key, fn, args in (
            ("soil", buildability.soil_septic, (shape, listing.lat, listing.lon)),
            ("wildfire", buildability.wildfire, (shape, listing.lat, listing.lon)),
            ("internet", buildability.internet, (listing.lat, listing.lon, state)),
            ("power_company", buildability.power_company, (listing.lat, listing.lon, state)),
        ):
            try:
                facts[key] = fn(*args)
            except Exception as e:  # noqa: BLE001 - best-effort extras must never sink a parcel
                facts[key] = None
                facts["errors"].append(f"{key}: {e}")
        return facts

    @staticmethod
    def _names(feats, keys):
        keys = (keys,) if isinstance(keys, str) else keys
        out = []
        for f in feats:
            a = f["attributes"]
            for k in keys:
                if a.get(k):
                    if a[k] not in out:
                        out.append(a[k])
                    break
            else:
                out.append("(unnamed)")
        return out

    # ---- water ----------------------------------------------------------------

    def wells(self, listing, parcel, state, facts):
        center = (listing.lon, listing.lat)
        if parcel and parcel.geometry:
            lat, lon = geo.polygon_centroid(parcel.geometry["rings"])
            center = (lon, lat)
        result = {"on_parcel": [], "decommissioned_on_parcel": 0, "nearby_count": 0,
                  "median_depth_ft": None, "median_gpm": None, "median_static_ft": None,
                  "nearest_ft": None}

        if state == "WA":
            feats = self._q(facts, "wa_wells", geometry=center, distance_m=WELL_SEARCH_RADIUS_M,
                            out_fields="WellProjectType,WellSubType,CompletedDepth,FlowRateGPM,"
                                       "StaticWaterLvl,ParcelNumber,WorkCompletionDate,WellTagNr",
                            return_geometry=True)
            rows = []
            for f in feats:
                a = f["attributes"]
                g = f.get("geometry") or {}
                rows.append({
                    "water": a.get("WellProjectType") == "Water",
                    "decom": a.get("WellProjectType") == "Decommissioning"
                             and "water" in (a.get("WellSubType") or "").lower(),
                    "depth": _num(a.get("CompletedDepth")), "gpm": _num(a.get("FlowRateGPM")),
                    "static": _num(a.get("StaticWaterLvl")), "pid": a.get("ParcelNumber"),
                    "year": _year(a.get("WorkCompletionDate")), "tag": a.get("WellTagNr"),
                    "x": g.get("x"), "y": g.get("y"),
                })
        else:
            feats = self._q(facts, "id_wells", geometry=center, distance_m=WELL_SEARCH_RADIUS_M,
                            out_fields="WellUse,TotalDepth,ProductionRate,StaticWaterLevel,"
                                       "ConstructionDate,WellID",
                            return_geometry=True)
            rows = []
            for f in feats:
                a = f["attributes"]
                g = f.get("geometry") or {}
                use = (a.get("WellUse") or "").lower()
                rows.append({
                    "water": any(u in use for u in ID_WATER_WELL_USES), "decom": False,
                    "depth": _num(a.get("TotalDepth")), "gpm": _num(a.get("ProductionRate")),
                    "static": _num(a.get("StaticWaterLevel")), "pid": None,
                    "year": _year(a.get("ConstructionDate")), "tag": a.get("WellID"),
                    "x": g.get("x"), "y": g.get("y"),
                })

        pid = norm_pid(parcel.parcel_id) if parcel else ""
        water = [r for r in rows if r["water"]]
        for r in rows:
            on = bool(pid and r["pid"] and norm_pid(r["pid"]) == pid)
            if not on and parcel and parcel.geometry and r["x"] is not None:
                on = geo.point_in_polygon(r["x"], r["y"], parcel.geometry["rings"])
            if on and r["water"]:
                result["on_parcel"].append(r)
            elif on and r["decom"]:
                result["decommissioned_on_parcel"] += 1

        result["nearby_count"] = len(water)
        result["median_depth_ft"] = _median([r["depth"] for r in water])
        result["median_gpm"] = _median([r["gpm"] for r in water])
        result["median_static_ft"] = _median([r["static"] for r in water])
        dists = [geo.haversine_m(center[1], center[0], r["y"], r["x"])
                 for r in water if r["x"] is not None and r not in result["on_parcel"]]
        if dists:
            result["nearest_ft"] = round(min(dists) / geo.METERS_PER_FOOT)
        result["within_quarter_mile"] = sum(1 for d in dists if d <= 402)
        return result

    # ---- electric proxy -------------------------------------------------------

    def address_points(self, listing, parcel, facts):
        """Addressed sites near the parcel (each is a home/building that has
        power, so a close neighbour means a short line extension)."""
        target = parcel.geometry if parcel and parcel.geometry else (listing.lon, listing.lat)
        feats = self._q(facts, "sc_address_points", geometry=target,
                        distance_m=NEIGHBOR_SEARCH_RADIUS_M, out_fields="FULLADDR",
                        return_geometry=True, max_records=500)
        on_parcel, dists = [], []
        for f in feats:
            g = f.get("geometry") or {}
            if g.get("x") is None:
                continue
            if parcel and parcel.geometry and geo.point_in_polygon(g["x"], g["y"], parcel.geometry["rings"]):
                on_parcel.append(f["attributes"].get("FULLADDR"))
                continue
            if parcel and parcel.geometry:
                d = _dist_point_polygon((g["x"], g["y"]), parcel.geometry)
            else:
                d = geo.haversine_m(listing.lat, listing.lon, g["y"], g["x"])
            dists.append(d)
        return {
            "on_parcel": on_parcel,
            "count": len(dists),
            "nearest_ft": round(min(dists) / geo.METERS_PER_FOOT) if dists else None,
        }

    # ---- access -----------------------------------------------------------------

    def roads(self, listing, parcel, state, facts):
        shape = parcel.geometry if parcel and parcel.geometry else None
        target = shape or (listing.lon, listing.lat)
        # Look tight first (frontage), then wider only if nothing touches, so
        # dense neighbourhoods don't hit the server's record cap.
        found = []
        for radius in (ROAD_FRONTAGE_TOLERANCE_M + 15 if shape else 75, ROAD_SEARCH_RADIUS_M):
            found = self._road_candidates(target, radius, state, facts)
            for r in found:
                if shape:
                    r["dist_m"] = geo.polygon_to_paths_m(shape["rings"], r["paths"])
                else:
                    r["dist_m"] = geo.point_to_paths_m(listing.lon, listing.lat, r["paths"])
                del r["paths"]
            if found:
                break
        found.sort(key=lambda r: r["dist_m"])

        if state == "WA" and found and shape:
            self._apply_road_log(shape, found, facts)
        return {"has_shape": bool(shape), "roads": found[:12]}

    def _road_candidates(self, target, radius, state, facts):
        found = []
        if state == "WA":
            for f in self._q(facts, "sc_streets", geometry=target, distance_m=radius,
                             out_fields="FullName,RoadClass,Jurisdiction", return_geometry=True,
                             max_records=1000):
                a = f["attributes"]
                cls = a.get("RoadClass") or ""
                found.append({"name": a.get("FullName") or "(unnamed)", "class": cls,
                              "public": None if cls in SC_PRIVATE_CLASSES else True,
                              "private_hint": cls in SC_PRIVATE_CLASSES,
                              "paths": (f.get("geometry") or {}).get("paths") or []})
        if not found:  # Idaho, or outside the county street network
            for layer in ("tiger_local_roads", "tiger_secondary_roads"):
                for f in self._q(facts, layer, geometry=target, distance_m=radius,
                                 out_fields="NAME,MTFCC", return_geometry=True, max_records=1000):
                    a = f["attributes"]
                    code = a.get("MTFCC") or ""
                    found.append({"name": a.get("NAME") or "(unnamed)",
                                  "class": TIGER_PUBLIC.get(code) or TIGER_PRIVATE.get(code) or code,
                                  "public": True if code in TIGER_PUBLIC else (False if code in TIGER_PRIVATE else None),
                                  "private_hint": code in TIGER_PRIVATE,
                                  "paths": (f.get("geometry") or {}).get("paths") or []})
        return found

    def _apply_road_log(self, shape, roads, facts):
        """The county road log says whether a road is County, City or Private."""
        log = self._q(facts, "sc_road_log", geometry=shape,
                      distance_m=ROAD_FRONTAGE_TOLERANCE_M + 15, out_fields="RoadName,JurDesc")
        jur = {_road_key(f["attributes"].get("RoadName")): f["attributes"].get("JurDesc") or ""
               for f in log}
        for r in roads:
            j = jur.get(_road_key(r["name"]))
            if not j:
                continue
            r["jurisdiction"] = j
            if "Private" in j or "not known" in j:
                r["public"], r["private_hint"] = False, True
            elif j in ("County", "City/municipal"):
                r["public"], r["private_hint"] = True, False


_DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _road_key(name):
    parts = (name or "").upper().replace(".", "").split()
    while parts and parts[0] in _DIRS:
        parts = parts[1:]
    while parts and parts[-1] in _DIRS:
        parts = parts[:-1]
    return " ".join(parts)


def _year(v):
    if isinstance(v, (int, float)) and abs(v) > 1_000_000_000:  # epoch milliseconds
        return (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=v)).year
    if isinstance(v, str) and re.match(r"\d{4}", v):
        return int(v[:4])
    return None


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


def _median(vals):
    nums = [float(v) for v in vals if isinstance(v, (int, float)) and v > 0]
    return round(statistics.median(nums), 1) if nums else None


def _dist_point_polygon(pt, geom):
    if not geom or not geom.get("rings"):
        return float("inf")
    if geo.point_in_polygon(pt[0], pt[1], geom["rings"]):
        return 0.0
    return geo.point_to_paths_m(pt[0], pt[1], geom["rings"])
