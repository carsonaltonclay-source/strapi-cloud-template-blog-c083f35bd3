"""Offline tests: python -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spokane_land import alerts, buildability, cost, geo, history, remarks  # noqa: E402
from spokane_land.analyze import analyze  # noqa: E402
from spokane_land.models import Listing, Parcel  # noqa: E402
from spokane_land.sources.csv_import import load_csv  # noqa: E402

# ~1 acre square (63.6 m sides) near Deer Park, clockwise like Esri rings.
LON, LAT = -117.45, 47.95
DLAT = 63.6 / 111320
DLON = 63.6 / (111320 * 0.669)
SQUARE = [[(LON, LAT), (LON, LAT + DLAT), (LON + DLON, LAT + DLAT), (LON + DLON, LAT), (LON, LAT)]]


def statuses(text, cat):
    return [e.status for e in remarks.scan_text(text)[cat]]


class GeoTests(unittest.TestCase):
    def test_haversine_spokane_to_post_falls(self):
        miles = geo.miles_between(47.6588, -117.4260, 47.7180, -116.9516)
        self.assertAlmostEqual(miles, 22.3, delta=0.6)

    def test_point_in_polygon(self):
        self.assertTrue(geo.point_in_polygon(LON + DLON / 2, LAT + DLAT / 2, SQUARE))
        self.assertFalse(geo.point_in_polygon(LON - DLON, LAT, SQUARE))

    def test_area(self):
        self.assertAlmostEqual(geo.polygon_area_acres(SQUARE), 1.0, delta=0.03)

    def test_polygon_to_road_distance(self):
        crossing = [[(LON - 0.001, LAT + DLAT / 2), (LON + 0.002, LAT + DLAT / 2)]]
        self.assertEqual(geo.polygon_to_paths_m(SQUARE, crossing), 0.0)
        # Road running north-south 100 m east of the parcel's east edge.
        east = LON + DLON + 100 / (111320 * 0.669)
        far = [[(east, LAT - 0.01), (east, LAT + 0.01)]]
        self.assertAlmostEqual(geo.polygon_to_paths_m(SQUARE, far), 100, delta=2)


class RemarksTests(unittest.TestCase):
    def test_water(self):
        self.assertIn("well", statuses("Drilled well producing 12 gpm.", "water"))
        self.assertIn("shared_well", statuses("Shared well in place with neighbor", "water"))
        self.assertNotIn("well", statuses("Shared well in place with neighbor", "water"))
        self.assertIn("public", statuses("City water at the street", "water"))
        self.assertIn("none", statuses("Buyer will need to drill a well.", "water"))

    def test_electric(self):
        self.assertIn("at_road", statuses("Power at the road!", "electric"))
        self.assertIn("on_site", statuses("Power to the property, 200 amp meter base.", "electric"))
        self.assertIn("none", statuses("Perfect off-grid retreat", "electric"))

    def test_septic(self):
        self.assertEqual(statuses("Perc test completed and septic design approved", "septic")[0], "approved")
        self.assertIn("failed", statuses("Lot failed perc in 2019", "septic"))
        self.assertIn("sewer", statuses("Public sewer available", "septic"))
        self.assertNotIn("mentioned", statuses("Septic design approved", "septic"))

    def test_access(self):
        self.assertIn("landlocked", statuses("Landlocked parcel, great for hunting", "access"))
        self.assertIn("easement", statuses("Access via recorded easement", "access"))
        self.assertIn("public_road", statuses("Paved county road frontage", "access"))

    def test_structured_fields(self):
        l = Listing(source="mls", id="1", water_source="Well", electric="At Road",
                    sewer="Septic Approved", road_access="County Road")
        ev = remarks.from_structured(l)
        self.assertEqual(ev["water"][0].status, "well")
        self.assertEqual(ev["electric"][0].status, "at_road")
        self.assertEqual(ev["septic"][0].status, "approved")
        self.assertEqual(ev["access"][0].status, "public_road")


class CsvTests(unittest.TestCase):
    def test_loose_headers(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("MLS #,List Price,LOT SIZE,Latitude,Longitude,Public Remarks,APN\n")
            f.write('123,"$89,500",87120,47.9,-117.4,"Power at road",12345.6789\n')
        try:
            [l] = load_csv(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(l.price, 89500)
        self.assertAlmostEqual(l.lot_acres, 2.0)
        self.assertEqual(l.parcel_id, "12345.6789")
        self.assertEqual(l.remarks, "Power at road")


def facts(**over):
    base = {
        "errors": [], "notes": [], "state": "WA",
        "parcel": Parcel(parcel_id="1", county="Spokane", state="WA", acres=5,
                         land_use="Vacant Land", geometry={"rings": SQUARE}, match="map pin inside parcel"),
        "wells": {"on_parcel": [], "decommissioned_on_parcel": 0, "nearby_count": 12,
                  "median_depth_ft": 240, "median_gpm": 15, "median_static_ft": 90,
                  "nearest_ft": 900, "within_quarter_mile": 1},
        "water_district": [], "city": [], "uga": [], "aquifer": False, "flood": [], "zoning": [],
        "neighbors": {"on_parcel": [], "count": 6, "nearest_ft": 400},
        "roads": {"has_shape": True, "roads": [
            {"name": "N Example Rd", "class": "Local", "public": True, "private_hint": False,
             "jurisdiction": "County", "dist_m": 0.0}]},
    }
    base.update(over)
    return base


class AnalyzeTests(unittest.TestCase):
    def listing(self, **kw):
        return Listing(source="t", id="t1", price=100000, lot_acres=5, lat=LAT, lon=LON, **kw)

    def test_gis_only(self):
        r = analyze(self.listing(), facts())
        self.assertEqual(r["water"]["status"], "needs_well")
        self.assertEqual(r["electric"]["status"], "likely_near")
        self.assertEqual(r["septic"]["status"], "required")
        self.assertEqual(r["access"]["status"], "public_road")
        self.assertEqual(r["price_per_acre"], 20000)

    def test_well_log_beats_remarks(self):
        f = facts(wells=dict(facts()["wells"], on_parcel=[{"tag": "ABC123", "year": 2001, "depth": 300, "gpm": 10}]))
        r = analyze(self.listing(remarks="No water. Buyer to drill a well."), f)
        self.assertEqual(r["water"]["status"], "well")
        self.assertTrue(any("Conflicting water" in x for x in r["flags"]))

    def test_landlocked(self):
        f = facts(roads={"has_shape": True, "roads": [
            {"name": "W Far Rd", "class": "Local", "public": True, "private_hint": False, "dist_m": 250.0}]})
        r = analyze(self.listing(), f)
        self.assertEqual(r["access"]["status"], "landlocked")
        self.assertIn("820 ft", r["access"]["evidence"][0]["detail"])
        # Seller says there is a recorded easement: that wins and isn't a conflict.
        r = analyze(self.listing(remarks="Access via recorded easement."), f)
        self.assertEqual(r["access"]["status"], "easement")
        self.assertFalse(any("Conflicting access" in x for x in r["flags"]))

    def test_neighbors_on_wells_downgrades_district(self):
        f = facts(water_district=["Example Water District"],
                  wells=dict(facts()["wells"], within_quarter_mile=6))
        self.assertEqual(analyze(self.listing(), f)["water"]["status"], "district_wells")

    def test_small_lot_and_aquifer_flags(self):
        f = facts(aquifer=True, flood=["AE"])
        l = Listing(source="t", id="t2", price=50000, lot_acres=0.5, lat=LAT, lon=LON)
        flags = " ".join(analyze(l, f)["flags"])
        self.assertIn("Aquifer", flags)
        self.assertIn("below the state minimum", flags)
        # On public water and sewer a small lot isn't a well/septic problem.
        l2 = Listing(source="t", id="t3", price=50000, lot_acres=0.5, lat=LAT, lon=LON,
                     water_source="Public", sewer="Public Sewer")
        self.assertNotIn("state minimum", " ".join(analyze(l2, facts())["flags"]))
        self.assertIn("flood", flags)

    def test_split_lot_well_on_parent_parcel(self):
        # 5-acre county parcel; the listing is a 0.12-acre lot whose pin is
        # ~80 m from the recorded well.
        well = {"tag": "W1", "year": 1997, "depth": 75, "gpm": None,
                "x": LON + DLON * 0.95, "y": LAT + DLAT * 0.95}
        f = facts(wells=dict(facts()["wells"], on_parcel=[well]))
        f["parcel"].geometry = None
        l = Listing(source="t", id="s", price=50000, lot_acres=0.12, lat=LAT + DLAT * 0.05, lon=LON + DLON * 0.05)
        r = analyze(l, f)
        self.assertNotEqual(r["water"]["status"], "well")
        self.assertTrue(any("split from it" in x for x in r["flags"]))
        # Same lot, well right next to the pin: still counts.
        well2 = dict(well, x=LON + DLON * 0.07, y=LAT + DLAT * 0.07)
        f = facts(wells=dict(facts()["wells"], on_parcel=[well2]))
        f["parcel"].geometry = None
        self.assertEqual(analyze(l, f)["water"]["status"], "well")

    def test_parcel_sketch(self):
        r = analyze(self.listing(), facts())
        xs = [p[0] for p in r["shape"]]
        self.assertTrue(4 <= len(r["shape"]) <= 48)
        self.assertAlmostEqual(max(xs) - min(xs), 64, delta=3)  # ~1-acre square, 63.6 m sides

    def test_steep_flag(self):
        t = {"slope_class": "steep", "flat_pct": 10, "slope_mean_pct": 38, "on_hill": True}
        r = analyze(self.listing(), facts(terrain=t))
        self.assertEqual(r["terrain"], t)
        self.assertTrue(any("Steep ground" in x for x in r["flags"]))

    def test_score_range(self):
        best = analyze(self.listing(water_source="Public", electric="On Property",
                                    sewer="Public Sewer", road_access="County Road"), facts())
        self.assertEqual(best["score"], 100)
        self.assertEqual(best["rating"], "Build-ready")


class BuildabilityTests(unittest.TestCase):
    def test_zoning_check(self):
        z = buildability.zoning_check
        self.assertEqual(z("Rural-5", 5)["status"], "ok")
        self.assertEqual(z("Rural-5", 20)["status"], "splittable")
        self.assertIn("4 lots", z("Rural-5", 20)["label"])
        self.assertEqual(z("Rural Traditional", 5)["status"], "undersized")
        self.assertEqual(z("Light Industrial", 5)["status"], "not_residential")
        self.assertNotEqual(z("Neighborhood Commercial", 5)["status"], "not_residential")  # SCC Table 612-1
        self.assertEqual(z("Low Density Residential", 0.2)["status"], "urban")
        self.assertIsNone(z("", 5))
        # Split-zoned parcel: the stricter zone governs.
        self.assertEqual(z("Rural-5, Rural Conservation", 12)["zone"], "Rural Conservation")

    def test_soil_and_fire_flags(self):
        l = Listing(source="t", id="b1", price=100000, lot_acres=5, lat=LAT, lon=LON)
        f = facts(soil={"rating": "Very limited", "worst": "Very limited", "outlook": "hard", "hard_limits": ["Depth to bedrock"]},
                  wildfire={"class": 4, "label": "High"}, zoning=["Rural Traditional"])
        r = analyze(l, f)
        flags = " ".join(r["flags"])
        self.assertIn("depth to bedrock", flags)
        self.assertIn("High wildfire", flags)
        self.assertIn("Smaller than Rural Traditional", flags)
        self.assertEqual(r["zoning_check"]["status"], "undersized")


class CostTests(unittest.TestCase):
    def result(self, water, electric, septic, access, price=100000):
        return {"price": price, "water": {"status": water}, "electric": {"status": electric},
                "septic": {"status": septic}, "access": {"status": access}}

    def test_ready_lot_costs_little(self):
        c = cost.all_in(self.result("well", "on_site", "installed", "public_road"), {})
        d = cost.DEFAULTS
        drive = d["driveway_base"] + d["default_driveway_ft"] * d["driveway_per_ft"]
        self.assertEqual(c["improvements"], drive + d["prep_flat"])
        # A known house site sets the driveway length and the grading class.
        site = {"site": {"driveway_ft": 400, "slope_pct": 18}}
        c = cost.all_in(self.result("well", "on_site", "installed", "public_road"), {"site": site})
        items = {i["key"]: i["cost"] for i in c["items"]}
        self.assertEqual(items["access"], d["driveway_base"] + 400 * d["driveway_per_ft"])
        self.assertEqual(items["prep"], d["prep_steep"])
        self.assertEqual(c["total"], 100000 + c["improvements"])

    def test_raw_land(self):
        f = {"wells": {"median_depth_ft": 200}, "neighbors": {"nearest_ft": 1150},
             "soil": {"rating": "Very limited", "outlook": "hard"}, "terrain": {"slope_class": "steep"}}
        c = cost.all_in(self.result("needs_well", "likely_near", "required", "landlocked"), f)
        d = cost.DEFAULTS
        items = {i["key"]: i["cost"] for i in c["items"]}
        self.assertEqual(items["water"], 200 * d["well_per_ft"] + d["well_system"])
        self.assertEqual(items["power"], d["power_service"] + 1000 * d["power_per_ft"])
        self.assertEqual(items["septic"], d["septic_engineered"])
        self.assertEqual(items["access"], d["landlocked_access"] + d["driveway_base"] + d["default_driveway_ft"] * d["driveway_per_ft"])
        self.assertEqual(items["prep"], d["prep_steep"])
        self.assertEqual(c["inputs"], {"well_ft": 200, "power_ft": 1150, "soil": "hard", "driveway_ft": None, "site_slope": None})
        f["soil"]["outlook"] = "design"
        c = cost.all_in(self.result("needs_well", "likely_near", "required", "landlocked"), f)
        self.assertEqual(c["items"][2]["cost"], d["septic_pressure"])

    def test_custom_assumptions(self):
        c = cost.all_in(self.result("needs_well", "on_site", "installed", "public_road"), {}, {"well_per_ft": 100})
        self.assertEqual(c["items"][0]["cost"], 300 * 100 + cost.DEFAULTS["well_system"])


class HistoryTests(unittest.TestCase):
    def r(self, id_, price, **kw):
        return dict({"id": id_, "price": price, "acres": 5, "parcel_id": None, "address": ""}, **kw)

    def test_dedupe(self):
        a = self.r("a", 100000, parcel_id="P1", address="21 S Harrison Rd Lot 1")
        b = self.r("b", 100000, parcel_id="P1", address="21 S Harrison Rd", remarks="long " * 50)
        c = self.r("c", 100000, parcel_id="P1", address="21 S Harrison Rd Lot 2")
        d = self.r("d", 250000, parcel_id="P1", address="other")
        out = history.dedupe([a, b, c, d])
        self.assertEqual([x["id"] for x in out], ["b", "c", "d"])
        self.assertIn("21 S Harrison Rd Lot 1", out[0]["also_listed_as"])
        # Identical copies: the same one wins whatever order they arrive in.
        x, y = self.r("x1", 5000, parcel_id="P9"), self.r("x2", 5000, parcel_id="P9")
        self.assertEqual(history.dedupe([dict(x), dict(y)])[0]["id"], history.dedupe([dict(y), dict(x)])[0]["id"])

    def test_refresh(self):
        first = [self.r("a", 100000, days_on_market=10), self.r("b", 50000)]
        info = history.apply(first, [], today="2026-10-01")
        self.assertTrue(info["first_run"])
        self.assertEqual(first[0]["first_seen"], "2026-09-21")
        self.assertFalse(first[0]["is_new"])
        second = [self.r("a", 90000), self.r("c", 70000)]
        info = history.apply(second, first, today="2026-10-08")
        self.assertEqual((info["new"], info["price_cuts"], info["removed"]), (1, 1, 1))
        self.assertEqual(second[0]["price_cut"], 10000)
        self.assertEqual(second[0]["first_seen"], "2026-09-21")
        self.assertEqual([h["price"] for h in second[0]["price_history"]], [100000, 90000])
        self.assertTrue(second[1]["is_new"])
        self.assertEqual(info["removed_list"][0]["id"], "b")


class AlertTests(unittest.TestCase):
    def test_saved_search_matching(self):
        def r(id_, **kw):
            base = {"id": id_, "price": 90000, "acres": 6, "miles_from_spokane": 12, "score": 80, "rating": "Build-ready",
                    "water": {"status": "well", "label": "Well"}, "electric": {"status": "at_road", "label": "At road"},
                    "access": {"status": "public_road"}, "septic": {"status": "required"},
                    "terrain": {"slope_class": "flat", "on_hill": False, "position": "flat"},
                    "wildfire": {"class": 2}, "soil": {"rating": "Very limited", "outlook": "design"}, "is_new": True}
            base.update(kw)
            return base
        results = [r("a"), r("b", price=300000), r("c", is_new=False), r("d", water={"status": "needs_well", "label": "Needs well"}),
                   r("e", is_new=False, price_cut=5000), r("f", wildfire={"class": 4})]
        st = {"max_price": 150000, "min_acres": 5, "radius": 25, "f": {"water": True, "fire": True}}
        groups = alerts.collect([{"name": "5ac", "notify": True, "state": st}, {"name": "off", "notify": False, "state": {}}], results)
        self.assertEqual(len(groups), 1)
        self.assertEqual([x["id"] for x in groups[0][1]], ["a", "e"])
        subject, txt, _ = alerts.render(groups, "https://example.test")
        self.assertIn("2 new matches", subject)
        self.assertIn("PRICE CUT", txt)


class VerdictTests(unittest.TestCase):
    def listing(self, **kw):
        return Listing(source="t", id="v1", price=100000, lot_acres=5, lat=LAT, lon=LON, **kw)

    def test_ready_lot_is_buildable(self):
        r = analyze(self.listing(water_source="Public", electric="On Property", sewer="Public Sewer"),
                    facts(site={"buildable_acres": 3.2, "buildable_pct": 80, "site": {"slope_pct": 3, "driveway_ft": 120}}))
        self.assertEqual(r["buildable"]["level"], "yes")
        self.assertEqual(r["cost"]["inputs"]["driveway_ft"], 120)

    def test_raw_land_needs_work(self):
        r = analyze(self.listing(), facts())
        self.assertEqual(r["buildable"]["level"], "work")
        self.assertIn("drill a well", r["buildable"]["reasons"][-1])

    def test_no_room_is_not_buildable(self):
        r = analyze(self.listing(), facts(site={"buildable_acres": 0.02, "buildable_pct": 1, "lost_to": {"hazard": 4.5}}))
        self.assertEqual(r["buildable"]["level"], "no")
        self.assertTrue(any(c["key"] == "room" and c["level"] == "bad" for c in r["checks"]))

    def test_landlocked_is_questionable(self):
        f = facts(roads={"has_shape": True, "roads": [
            {"name": "W Far Rd", "class": "Local", "public": True, "private_hint": False, "dist_m": 250.0}]})
        r = analyze(self.listing(), f)
        self.assertEqual(r["buildable"]["level"], "doubt")

    def test_red_flags_from_text(self):
        self.assertEqual([x["key"] for x in remarks.red_flags("HOA $300/yr, CC&Rs, no mobile homes. Not buildable.")],
                         ["unbuildable", "hoa", "ccrs", "no_mobile"])


class AuditRegressionTests(unittest.TestCase):
    def test_text_false_positives(self):
        self.assertEqual(statuses("Good well-drained soils. Views as well in a quiet area.", "water"), [])
        self.assertEqual(statuses("New well-maintained county road", "water"), [])
        self.assertEqual(statuses("Power in the area.", "electric"), ["at_road"])
        self.assertEqual(statuses("No perc test done yet, buyer to verify.", "septic"), ["needed"])
        self.assertEqual(statuses("Perc test failed in 2019", "septic"), ["failed"])
        self.assertIn("approved", statuses("road cut in, perc tests for septic have been done", "septic"))
        self.assertIn("well", statuses("60 GPM WELL- owner financing", "water"))
        self.assertNotIn("approved", statuses("No perc test has been done", "septic"))

    def test_structured_negations(self):
        f = remarks.from_structured(Listing(source="t", id="n", water_source="No Well", sewer="No Sewer", electric="No Power"))
        self.assertEqual([e.status for e in f["water"]], ["none"])
        self.assertEqual([e.status for e in f["septic"]], ["required"])
        self.assertEqual([e.status for e in f["electric"]], ["none"])

    def test_water_rights_is_not_a_status(self):
        r = analyze(Listing(source="t", id="w", price=1, lot_acres=5, remarks="Comes with water rights."), {"errors": [], "notes": []})
        self.assertEqual(r["water"]["status"], "unknown")

    def test_unknown_parcel_is_not_buildable(self):
        r = analyze(Listing(source="t", id="u", price=1000, lot_acres=5), {"errors": [], "notes": []})
        self.assertEqual(r["buildable"]["level"], "doubt")
        self.assertEqual(r["buildable"]["label"], "Not enough information")


class ExportAndInputTests(unittest.TestCase):
    def test_pack_respects_document_limit(self):
        from spokane_land.report import pack
        items = [{"x": "a" * 1000} for _ in range(50)]
        groups = pack(items, limit=5000)
        self.assertEqual(sum(len(g) for g in groups), 50)
        import json as _j
        self.assertTrue(all(len(_j.dumps({"items": g}, separators=(",", ":"))) <= 5000 for g in groups))

    def test_csv_ids_stable_without_id_column(self):
        rows = "Address,City,Price,Latitude,Longitude\n1 A Rd,Elk,100,47.9,-117.3\n2 B Rd,Elk,200,47.8,-117.2\n"
        rev = "Address,City,Price,Latitude,Longitude\n2 B Rd,Elk,200,47.8,-117.2\n1 A Rd,Elk,100,47.9,-117.3\n"
        ids = []
        for text in (rows, rev):
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
                f.write(text)
            ids.append({l.address: l.id for l in load_csv(f.name)})
            os.unlink(f.name)
        self.assertEqual(ids[0], ids[1])

    def test_parcel_file_lot_numbers_and_zip(self):
        from spokane_land.sources.parcels import load_parcel_ids, parse_entry
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# my list\n12345 N Example Rd Lot #4, Deer Park, WA | 89000\n39352.9067  # the creek one\n")
        got = load_parcel_ids(f.name)
        os.unlink(f.name)
        self.assertEqual(got[0].address, "12345 N Example Rd Lot #4, Deer Park, WA")
        self.assertEqual(got[0].price, 89000)
        self.assertEqual(got[1].parcel_id, "39352.9067")
        l = parse_entry("https://www.zillow.com/homedetails/12345-N-Example-Rd-Deer-Park-WA/123_zpid/")
        self.assertEqual(l.zip, "")


if __name__ == "__main__":
    unittest.main()


class ThirdAuditTests(unittest.TestCase):
    def test_frontage_wording(self):
        for t in ("Frontage on the lake. Road is gravel.", "frontage on the river and road"):
            self.assertNotIn("public_road", statuses(t, "access"), t)
        self.assertEqual(statuses("300 ft of frontage on Deer Lake with a private drive", "access"), ["private_road"])
        for t in ("Frontage on Highway 395", "300 ft frontage on SR 291", "frontage on Elk-Chattaroy Rd"):
            self.assertIn("public_road", statuses(t, "access"), t)

    def test_perc_negation_is_per_clause(self):
        for t in ("Perc tested 2023, no perc issues.", "Septic design approved; no soil logs needed.",
                  "No perc test; septic design approved"):
            self.assertIn("approved", statuses(t, "septic"), t)
        for t in ("No perc test done yet", "has not been perc tested"):
            self.assertNotIn("approved", statuses(t, "septic"), t)

    def test_mls_values(self):
        f = remarks.from_structured(Listing(source="t", id="m", water_source="Private Well, Not Shared",
                                            electric="No Power at Site; Power at Road", sewer="Public Sewer Not Available"))
        self.assertEqual([e.status for e in f["water"]], ["well"])
        self.assertEqual([e.status for e in f["electric"]], ["at_road"])
        self.assertEqual([e.status for e in f["septic"]], ["required"])
        f = remarks.from_structured(Listing(source="t", id="m", water_source="Well - Individual; No HOA", electric="Power Not Available"))
        self.assertEqual([e.status for e in f["water"]], ["well"])
        self.assertEqual([e.status for e in f["electric"]], ["none"])

    def test_lot_on_parent_parcel_is_not_simply_buildable(self):
        l = Listing(source="t", id="p", price=100000, lot_acres=5, lat=LAT, lon=LON,
                    water_source="Public", electric="On Property", sewer="Public Sewer")
        f = facts(site={"buildable_acres": 0.01, "buildable_pct": 1, "parcel_acres": 40,
                        "site": {"slope_pct": 3, "driveway_ft": 900}})
        f["parcel"].geometry = None
        f["parcel"].acres = 40
        r = analyze(l, f)
        self.assertEqual(r["buildable"]["level"], "work")
        self.assertIsNone(r["site"]["site"])
        self.assertIsNone(r["assessed"])
        self.assertNotEqual(r["cost"]["inputs"]["driveway_ft"], 900)

    def test_failed_water_map_is_not_needs_well_evidence(self):
        f = facts(water_district=None)
        f["failed"] = {"sc_water_districts"}
        r = analyze(Listing(source="t", id="f", price=1, lot_acres=5, lat=LAT, lon=LON), f)
        self.assertTrue(all(e["confidence"] != "medium" for e in r["water"]["evidence"] if e["status"] == "needs_well"))

    def test_listing_date_separate_from_first_seen(self):
        prev = [{"id": "a", "price": 1, "first_seen": "2026-09-01"}]
        cur = [{"id": "a", "price": 1, "days_on_market": 3}, {"id": "b", "price": 1, "days_on_market": 400},
               {"id": "c", "price": 1}]
        history.apply(cur, prev, today="2026-10-08")
        self.assertEqual(cur[1]["first_seen"], "2026-10-08")
        self.assertEqual(cur[1]["listed"], "2025-09-03")
        self.assertIsNone(cur[2]["listed"])

    def test_csv_ids_distinct_for_same_address(self):
        rows = "Address,City,Price,Acres\nTBD Elk Rd,Deer Park,50000,5\nTBD Elk Rd,Deer Park,60000,10\n,,,\nweird,\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(rows)
        ls = load_csv(fh.name)
        os.unlink(fh.name)
        self.assertEqual(len({l.id for l in ls if l.address == "TBD Elk Rd"}), 2)


class ListingPhotoTests(unittest.TestCase):
    def test_photo_columns(self):
        rows = ("Address,City,Price,Acres,Photo URL,Photo Count\n"
                "1 A Rd,Elk,100,5,https://photos.example.com/a-p_e.jpg,12\n2 B Rd,Elk,200,5,javascript:alert(1),\n")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(rows)
        ls = {l.address: l for l in load_csv(fh.name)}
        os.unlink(fh.name)
        self.assertEqual((ls["1 A Rd"].photo_url, ls["1 A Rd"].photo_count), ("https://photos.example.com/a-p_e.jpg", 12))
        self.assertEqual((ls["2 B Rd"].photo_url, ls["2 B Rd"].photo_count), ("", None))

    def test_zillow_thumbnail_size(self):
        from spokane_land.listing_photos import _source_url
        self.assertEqual(_source_url("https://photos.zillowstatic.com/fp/abc-p_e.jpg"),
                         "https://photos.zillowstatic.com/fp/abc-cc_ft_384.jpg")
        self.assertEqual(_source_url("https://example.com/x.jpg"), "https://example.com/x.jpg")

    def test_csv_escaped_quotes(self):
        rows = 'Address,Price,Remarks,Photo URL\n1 A Rd,100,"He said ""perc approved"" twice",https://p.example.com/1.jpg\n2 B Rd,200,plain,https://p.example.com/2.jpg\n'
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(rows)
        ls = {l.address: l for l in load_csv(fh.name)}
        os.unlink(fh.name)
        self.assertEqual(ls["1 A Rd"].remarks, 'He said "perc approved" twice')
        self.assertEqual(ls["1 A Rd"].photo_url, "https://p.example.com/1.jpg")


class DataGapTests(unittest.TestCase):
    def test_sold_comps(self):
        from spokane_land import sold_comps
        sales = [{"price": 50000 + i * 1000, "acres": 5 + i * 0.2, "lat": LAT + i * 0.001, "lon": LON, "date": "2026-05-01",
                  "url": f"https://example.com/{i}", "zpid": str(i)} for i in range(6)]
        sales.append({"price": 900000, "acres": 5, "lat": LAT, "lon": LON, "date": "2019-01-01", "url": "", "zpid": "old"})
        c = sold_comps.comparable(LAT, LON, 5, sales, listing_mls="zillow-0", today=__import__("datetime").date(2026, 10, 7))
        self.assertEqual(c["n"], 5)  # its own earlier sale and the 2019 sale are left out
        self.assertEqual(c["radius_mi"], 3)
        self.assertTrue(40000 < c["est_value"] < 60000)
        self.assertTrue(all(e["url"] for e in c["examples"]))

    def test_owner_names_compare_loosely(self):
        from spokane_land.assemble import _improved, _norm_owner
        self.assertEqual(_norm_owner("Koppe, J.D. & C.R."), _norm_owner("KOPPE J D & C R"))
        self.assertTrue(_improved("Single Unit"))
        self.assertFalse(_improved("Vacant Land"))
