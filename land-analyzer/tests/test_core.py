"""Offline tests: python -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spokane_land import geo, remarks  # noqa: E402
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
        self.assertIn("under 1 acre", flags)
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


if __name__ == "__main__":
    unittest.main()
