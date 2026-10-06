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

It also answers "can I actually build here, and what will it cost?":

| Check | Source |
| --- | --- |
| **All-in estimate**: price + well, power line, septic, driveway, site prep | `cost.py` defaults (editable in the app) using nearby well depths, distance to the nearest home, soil rating and slope |
| **Septic soils**: Not / Somewhat / Very limited for drain fields, with the reasons | USDA NRCS Soil Data Access |
| **Zoning**: minimum lot size per home, room to split, zones that bar houses | Spokane County zoning code tables 616-3 and 618-3 |
| **Wildfire hazard** (very low … very high) | USFS Wildfire Hazard Potential 2023 |
| **Internet**: fastest measured home connection within a mile (WA) | Ookla speed tests via the WA State Broadband Office |
| **Power company** with phone number (WA) | WA electric retail service territories |
| **Drive time** to downtown Spokane | OSRM routing (public server) |

Every parcel also gets an **"Is it actually buildable?"** report and verdict
(Buildable / Buildable with work / Questionable / Likely not buildable):

| Part | How |
| --- | --- |
| **Room to build** | grid of points over the parcel; drops setbacks, wetlands, FEMA flood zones, landslides, stream buffers and slopes over 30% (`homesite.py`); picks the best house site |
| **Driveway & grading** | driveway length from the nearest road to that site × $/ft; grading cost from the slope at the site |
| **Flood & drainage** | FEMA National Flood Hazard Layer (both states) + USDA soil drainage class / hydric soils |
| **Deal-breakers** | wetlands (USFWS NWI), landslides (WA DNR), stream buffers (Spokane Co.), active mines (WA DNR; USGS topo pits in ID), well nitrate (WA DNR, Spokane Co., Idaho DEQ), and listing text: HOA, covenants, no manufactured homes, "not buildable", line easements (`dealbreakers.py`, `remarks.red_flags`) |
| **Is the price fair?** | Spokane County vacant-land sales within 3–6 miles, similar size, last 3 years, plus the assessed land value (`comps.py`); Idaho doesn't publish sale prices |
| **Monthly cost** | land-loan payment + property tax, with your own down payment / rate / term in the app |
| **Cell service** | Ookla mobile speed tests (Esri Living Atlas) |
| **Zoning outside Spokane County** | WA Zoning Atlas (Stevens, Lincoln, Pend Oreille, towns) and Bonner County; Kootenai County's server was down |
| **County rules** | setbacks, permit offices, well-water limits (`county_rules.py`) |
| **Aerial photo** | USDA NAIP via USGS, with the parcel line and house site (`--photos MILES`, needs Pillow) |

Output: `output/land_report.html` (interactive map + filterable table),
`land_report.csv` (open in Excel/Sheets) and `land_report.json`.

## The app

**Spokane Land Finder:** https://claude.ai/artifact/Phe43Pu3ykK6Cnz3hVGuQD

Open it on your phone or computer. It shows every analyzed listing on a map and
in a ranked list with filters (price, acres, has water, power close, not
landlocked, septic-friendly soil, low fire risk, new or price cut, starred) and
sorting by all-in cost or drive time. Tap **Why? Show the evidence** on any listing to see where
each answer came from. You can star listings, keep notes, track each parcel's status (Interested →
Called seller → Visited → Perc / due diligence → Offer made), download a
one-page report for your agent or lender, change the
cost assumptions behind the all-in estimates (saved in your browser), and
**save searches**: each saved search shows how many new listings match it, and
searches with "Email me new matches" get an email after each refresh.

To check a property you found somewhere else, use **Add a property to check**
(parcel number, address, listing link, or coordinates), then ask Claude to
"analyze my added properties". To pull fresh listings, ask Claude to "refresh my
land listings".

How a refresh works (for Claude or anyone maintaining it):

1. Read the app's `requests` collection (ArtifactData `list`) and write the
   pending ones to `requests.json` as `[{"id", "text", "price", "note"}]`.
2. Save the current `chunks` collection (ArtifactData `list` with `out_dir: prev`)
   and run `python -m spokane_land --csv <exports> --requests requests.json --previous prev --db-export dbx`.
   `--previous` marks listings that are new since the last refresh, records
   price cuts (`price_history`), and lists listings that disappeared
   (`meta.refresh`). Duplicate listings of the same parcel are merged.
3. Upload `dbx/chunks/*.json` to the `chunks` collection, `dbx/listings/*.json`
   to `listings` and `dbx/meta.json` to `meta/info` (ArtifactData `batch`),
   delete chunk documents beyond the new count, and mark processed requests
   `status: "done"`.
4. Alerts: read the `searches` collection to `searches.json` and run
   `python -m spokane_land.alerts searches.json output/land_report.json --out alert`;
   if anything matched, email `alert.html` to the owner.

Refreshes run when asked. A hands-off daily refresh needs a listing feed you
are allowed to pull automatically — the MLS RESO feed (`--reso`) through an
agent; scraping listing sites on a schedule breaks their terms.

The page is `app/land_finder.html`, built from `app/app_template.html` plus the
vector base map `app/basemap.json`:
`python -m spokane_land.basemap --app app/basemap.json app/land_finder.html`.
The map's shaded relief, `app/hillshade.jpg`, is a USGS 3DEP "Hillshade
Multidirectional" export for the same bounding box (published alongside the
page).

## Run it

Python 3.9+ only; no packages to install.

```bash
cd land-analyzer

# Pull what Redfin's download offers (partial — see "Where listings come from")
python -m spokane_land --redfin

# Import a CSV export of listings (MLS export, a spreadsheet, etc.)
python -m spokane_land --csv my_listings.csv

# Analyse specific properties you found anywhere: one parcel #, address,
# listing link or "lat, lon" per line (see sources/parcels.py)
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
| Septic suitability of soils | USDA NRCS Soil Data Access (`sdmdataaccess.sc.egov.usda.gov`) |
| Wildfire Hazard Potential | USFS via `imagery.geoplatform.gov` |
| Internet speed tests, electric service territories | WA State Broadband Office / WA UTC (ArcGIS Online) |
| Drive times | OSRM public server (`router.project-osrm.org`) |

Responses are cached for 24 h in `~/.cache/spokane-land` (`--no-cache` to
skip; `LAND_ANALYZER_CACHE` to move it).

## Accuracy notes

* Electric service has no public map. "Power likely close" means a home or
  addressed building sits within ~600 ft, not that a line reaches the lot.
* A water-district boundary doesn't guarantee a main on your road; if several
  private wells are nearby, the report says so.
* "Possibly landlocked" means no mapped road touches the parcel. A recorded
  easement may still give legal access — check the title report.
* The all-in estimate is a planning number, not a bid. Well depth and power
  distance come from neighbors, so a dry hole or a long line extension can
  cost far more.
* The USDA soil rating is mapped at a county scale; a site's actual soil logs
  decide the septic design.
* Zoning minimums apply to new lots. An older, smaller lot of record is often
  still buildable — ask the county.
* Internet and power-company data cover Washington only.
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
