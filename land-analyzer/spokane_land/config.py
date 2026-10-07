"""Search area and public data endpoints."""

# Downtown Spokane (Riverfront Park).
SPOKANE_LAT = 47.6588
SPOKANE_LON = -117.4260
DEFAULT_RADIUS_MILES = 25.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 spokane-land-analyzer/1.0"
)

SPOKANE_GIS = "https://gismo.spokanecounty.org/arcgis/rest/services"
ECOLOGY_GIS = "https://gis.ecology.wa.gov/serverext/rest/services"
IDWR_GIS = "https://gis.idwr.idaho.gov/hosting/rest/services"
TIGER_GIS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"

LAYERS = {
    # Spokane County Assessor parcel polygons (PID_NUM, acreage, site_address).
    "sc_parcels": f"{SPOKANE_GIS}/Assessor/Parcels/MapServer/0",
    # Owner, land use ("Vacant Land", ...), last sale.
    "sc_property": f"{SPOKANE_GIS}/SCOUT/PropertyLookup/MapServer/0",
    # Assessed land value (current assessment year).
    "sc_assessed": f"{SPOKANE_GIS}/Assessor/SCOUTSimple/MapServer/0",
    # Public water purveyor service areas.
    "sc_water_districts": f"{SPOKANE_GIS}/OpenData/Boundary/MapServer/10",
    "sc_municipal": f"{SPOKANE_GIS}/OpenData/Boundary/MapServer/3",
    "sc_uga": f"{SPOKANE_GIS}/OpenData/Planning/MapServer/1",
    "sc_zoning": f"{SPOKANE_GIS}/OpenData/Planning/MapServer/0",
    "sc_aquifer": f"{SPOKANE_GIS}/OpenData/Environment/MapServer/2",
    "sc_flood": f"{SPOKANE_GIS}/OpenData/Boundary/MapServer/14",
    # Regional street centerlines (covers Spokane Co. plus fringes of
    # Lincoln, Stevens/Pend Oreille, Bonner, Whitman, Benewah counties).
    "sc_streets": f"{SPOKANE_GIS}/OpenData/Transportation/MapServer/1",
    # County road log with public/private jurisdiction.
    "sc_road_log": f"{SPOKANE_GIS}/OpenData/Transportation/MapServer/0",
    # Every addressed structure/site in Spokane County.
    "sc_address_points": f"{SPOKANE_GIS}/OpenData/Property/MapServer/12",
    # WA Dept. of Ecology well reports (statewide) and statewide parcels.
    "wa_wells": f"{ECOLOGY_GIS}/WR/Well_Construction_Map_Service/MapServer/16",
    "wa_parcels": f"{ECOLOGY_GIS}/WR/Well_Construction_Map_Service/MapServer/123",
    # Idaho Dept. of Water Resources wells, parcels, municipal water areas.
    "id_wells": f"{IDWR_GIS}/Groundwater/Wells/MapServer/0",
    "id_parcels": f"{IDWR_GIS}/Reference/Parcels/MapServer/0",
    "id_water_areas": f"{IDWR_GIS}/Reference/MunicipalServiceAreaBoundaries/MapServer/0",
    # US Census TIGER roads (both states) for areas outside the county network.
    "tiger_local_roads": f"{TIGER_GIS}/Transportation/MapServer/8",
    "tiger_secondary_roads": f"{TIGER_GIS}/Transportation/MapServer/6",
    # Outside Spokane County: zoning, power company, internet.
    "wa_zoning_atlas": "https://services6.arcgis.com/tboeqGwETr5ppr5Q/arcgis/rest/services/WAZA_Prototype_Layers/FeatureServer/0",
    "bonner_zoning": "https://cloudgis.bonnercountyid.gov/server/rest/services/Map_Services/ZoningLanduse_Public/MapServer/2",
    # Kootenai County (unincorporated) zoning: read LABEL, not the stale ZONE_NAME codes.
    # Kootenai County assessor parcels: owner (Name), Acres, Gross_Val (assessed market value).
    "kootenai_parcels": "https://map.kcgov.us/arcgis/rest/services/NewServices/Data_Layers/MapServer/8",
    "kootenai_zoning": "https://map.kcgov.us/arcgis/rest/services/NewServices/Data_Layers/MapServer/21",
    "cda_zoning": "https://gis.cdaid.org/server/rest/services/Share/Planning/MapServer/8",
    "postfalls_zoning": "https://gis.postfalls.gov/server/rest/services/GIS2/LandUse/MapServer/5",
    "us_electric_territories": "https://services3.arcgis.com/OYP7N6mAJJCyH6hd/arcgis/rest/services/Electric_Retail_Service_Territories_HIFLD/FeatureServer/0",
    "ookla_fixed_tiles": "https://services.arcgis.com/jIL9msH9OI208GCb/arcgis/rest/services/Speedtest_by_Ookla_Global_Fixed_and_Mobile_Network_Performance_Map_Tiles/FeatureServer/0",
    "ookla_mobile_tiles": "https://services.arcgis.com/jIL9msH9OI208GCb/arcgis/rest/services/Speedtest_by_Ookla_Global_Fixed_and_Mobile_Network_Performance_Map_Tiles/FeatureServer/1",
    "wa_wria": "https://gis.ecology.wa.gov/serverext/rest/services/Authoritative/ECY/MapServer/11",
    "sc_permits_open": f"{SPOKANE_GIS}/BPPublic/BPPublic/MapServer/5",
    "sc_permits_2023": f"{SPOKANE_GIS}/ISDSDE/LandCapacityAnalysis/MapServer/13",
    # FEMA National Flood Hazard Layer, flood hazard zones (both states).
    "fema_flood": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
    # Deal-breaker checks.
    "nwi_wetlands": "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0",
    "wa_landslides": "https://gis.dnr.wa.gov/site3/rest/services/Geology/Landslide_Inventory_Database/MapServer/131",
    "sc_stream_buffers": f"{SPOKANE_GIS}/ISDSDE/LandCapacityAnalysis/MapServer/4",
    "wa_mines": "https://gis.dnr.wa.gov/site1/rest/services/Public_Geology/Active_Surface_Mine_Permit_Sites/MapServer/0",
    "usgs_mine_points": "https://energy.usgs.gov/arcgis/rest/services/Hosted/USMin_Prospect_and_mine_related_map_features/FeatureServer/17",
    "wa_groundwater_chem": "https://gis.dnr.wa.gov/site1/rest/services/Public_Geology/Groundwater_Chemistry/MapServer/0",
    "sc_nitrate_55": f"{SPOKANE_GIS}/WaterResources/WRIA55_LittleSpokane/MapServer/2",
    "sc_nitrate_56": f"{SPOKANE_GIS}/WaterResources/WRIA56_HangmanCreek/MapServer/2",
    "sc_nitrate_57": f"{SPOKANE_GIS}/WaterResources/WRIA57_MiddleSpokane/MapServer/1",
    "id_nitrate_wells": "https://mapcase.deq.idaho.gov/arcgis/rest/services/NPA_2020_WMS/MapServer/2",
    "tiger_school_unified": f"{TIGER_GIS}/School/MapServer/0",
    # WA State Broadband Office: measured speed-test tiles and electric utility territories.
    "wa_speed_tiles": "https://services6.arcgis.com/tboeqGwETr5ppr5Q/ArcGIS/rest/services/Ookla_Fixed_Tiles_WA_Q1_2019_Q1_2023/FeatureServer/50",
    "wa_electric_territories": "https://services6.arcgis.com/tboeqGwETr5ppr5Q/ArcGIS/rest/services/Electric_Retail_Service_Territories/FeatureServer/0",
}

ELEVATION_IMAGE_SERVER = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
SDA_URL = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
WILDFIRE_IMAGE_SERVER = "https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/USFS_EDW_RMRS_WildfireHazardPotentialClassified/ImageServer"
OSRM_URL = "https://router.project-osrm.org"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
REDFIN_CSV = "https://www.redfin.com/stingray/api/gis-csv"

# Analysis thresholds.
ROAD_FRONTAGE_TOLERANCE_M = 20      # road centerline this close to the parcel edge = clearly fronts it
# A lot fronts a road at its right-of-way line, not the centerline: half a typical 60-80 ft county
# right-of-way plus mapping slop is ~30 m; highways have 100-300 ft rights-of-way.
FRONTAGE_LOCAL_M = 30
FRONTAGE_HIGHWAY_M = 45
ROAD_SEARCH_RADIUS_M = 800          # how far to look for the nearest road
NEIGHBOR_SEARCH_RADIUS_M = 800      # how far to look for addressed structures (power proxy)
WELL_SEARCH_RADIUS_M = 1609         # 1 mile, for nearby-well depth / yield stats
POINT_PARCEL_SNAP_M = 40            # listing pin may sit in the road; snap to nearest parcel

SQFT_PER_ACRE = 43560.0
