# Spokane Land Analyzer

Finds land for sale within 25 miles of downtown Spokane and reports, for every
listing:

| Column | How it's determined |
| --- | --- |
| **Price**, acres, $/acre, miles from Spokane | Listing + county parcel area |
| **Water** | MLS *WaterSource* field → listing text → WA Ecology / Idaho IDWR **well logs on the parcel** → public water-district boundaries → nearby-well depth & yield |
| **Electric** | MLS *Electric* field → listing text ("power at road", "off grid"...) → assessor land use → distance to the nearest addressed structure (homes have power) |
| **Septic / sewer** | MLS *Sewer* field → listing text ("perc approved", "failed perc"...) → inside a city / urban growth area (sewer possible) or not (septic required) |
| **Landlocked** | MLS *RoadFrontageType* → listing text ("landlocked", "recorded easement") → **parcel boundary vs. road centerlines** (does any road touch the lot? public or private per the county road log) |

Each answer carries a **confidence** (high = stated by the seller/MLS or a
recorded well log on the parcel; medium = strong map evidence; low = proxy) and
the evidence behind it. Listings get a 0–100 **buildability score** (25 points
per category) and **flags** for things like FEMA flood zones, the Spokane
aquifer (stricter septic rules), lots under 1 acre (too small for a well +
septic under WAC 246-272A), conflicting information, or a map pin that
didn't land on a parcel.

Output: `output/land_report.html` (interactive map + filterable table),
`land_report.csv` (open in Excel/Sheets) and `land_report.json`.

## Run it

Python 3.9+ only; no packages to install.

```bash
cd land-analyzer

# Pull what Redfin's download offers (partial — see "Where listings come from")
python -m spokane_land --redfin

# Import a CSV export of listings (MLS export, a spreadsheet, etc.)
python -m spokane_land --csv my_listings.csv

# Analyse specific parcels you found anywhere (FSBO signs, LandWatch, Zillow)
python -m spokane_land --parcels parcels.txt

# Full MLS coverage through a RESO Web API feed
export RESO_BASE_URL=https://.../Reso/OData RESO_TOKEN=...
python -m spokane_land --reso

# Combine sources and filter
python -m spokane_land --redfin --csv my_listings.csv --max-price 150000 --min-acres 2 --radius 25
```

Try it with the bundled demo file (made-up listings on real parcels):
`python -m spokane_land --csv examples/sample_listings.csv`

Open `output/land_report.html` in a browser. Click a row (or a map dot) to see
the evidence for each verdict, the parcel number, and a link to the county
record.

## Where listings come from

There is no free, public feed of *every* land listing. The tool supports four
inputs; for complete coverage use the MLS feed or CSV exports:

1. **`--reso` (complete):** Spokane REALTORS® MLS, NWMLS and Coeur d'Alene MLS
   publish RESO Web API feeds. A licensed agent/broker can get IDX or VOW
   credentials (often via Spark, Bridge, Trestle or MLS Grid). These listings
   include structured *WaterSource / Electric / Sewer / RoadFrontageType*
   fields, so they get the most reliable results.
2. **`--csv FILE`:** any CSV. Column names are matched loosely (`List Price`,
   `Price`, `Acres`, `Lot Size`, `Latitude`, `APN`, `Parcel Number`,
   `Public Remarks`, `Water Source`, ...; see `sources/csv_import.py`). Rows
   need a price plus coordinates, a street address, or a parcel number. Ask an
   agent for an MLS export of active land within 25 miles — that is the
   easiest way to get everything.
3. **`--parcels FILE`:** one parcel number per line, optionally `, price`.
   Works for Spokane County (e.g. `39352.9067`), other WA counties, and Idaho.
4. **`--redfin` (partial):** Redfin's "Download All" CSV. The local MLSs
   don't allow their listings in that download, so it only returns the few
   land listings from other MLSs. Check Redfin's terms before automating it.

## Public data used

| Data | Source |
| --- | --- |
| Parcels, land use, owner, water districts, city limits, UGAs, zoning, aquifer, flood zones, street centerlines, road log, address points | Spokane County GIS (`gismo.spokanecounty.org`) |
| Well reports (depth, yield, static level, parcel #), statewide parcels | WA Dept. of Ecology |
| Wells, parcels, municipal water service areas (Kootenai Co.) | Idaho Dept. of Water Resources |
| Roads outside the county network | US Census TIGERweb |
| Address → coordinates | US Census geocoder |

Responses are cached for 24 h in `~/.cache/spokane-land` (`--no-cache` to
skip; `LAND_ANALYZER_CACHE` to move it).

## Accuracy notes

* Electric service has no public map. "Power likely close" means a home or
  addressed building sits within ~600 ft, not that a line reaches the lot.
* A water-district boundary doesn't guarantee a main on your road; if several
  private wells are nearby, the report says so.
* "Possibly landlocked" means no mapped road touches the parcel. A recorded
  easement may still give legal access — check the title report.
* Septic feasibility needs soil logs from the health district (Spokane
  Regional Health District, Northeast Tri County, Panhandle, ...).

Treat the report as a screening tool and verify before you make an offer.

## Development

```bash
python -m unittest discover -s tests     # offline tests
```

Layout: `sources/` (listing inputs), `enrich.py` (GIS lookups),
`remarks.py` (text + MLS field parsing), `analyze.py` (verdicts, score, flags),
`report.py` + `report_template.html` (outputs), `config.py` (center, radius,
endpoints, thresholds).
