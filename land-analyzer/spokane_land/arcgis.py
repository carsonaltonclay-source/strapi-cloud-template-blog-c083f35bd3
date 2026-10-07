"""Minimal ArcGIS REST query client."""

import json

from .http import HttpError, get_json

MAX_PAGES = 10


def _geom_params(geometry, distance_m=None):
    if isinstance(geometry, tuple):  # (lon, lat)
        params = {
            "geometry": f"{geometry[0]},{geometry[1]}",
            "geometryType": "esriGeometryPoint",
        }
    elif "rings" in geometry:
        params = {"geometry": json.dumps(geometry), "geometryType": "esriGeometryPolygon"}
    elif "paths" in geometry:
        params = {"geometry": json.dumps(geometry), "geometryType": "esriGeometryPolyline"}
    else:  # envelope
        params = {"geometry": json.dumps(geometry), "geometryType": "esriGeometryEnvelope"}
    params.update({"inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
    if distance_m:
        params.update({"distance": distance_m, "units": "esriSRUnit_Meter"})
    return params


def query(layer_url, geometry=None, where="1=1", out_fields="*", distance_m=None,
          return_geometry=False, max_records=None):
    """Return list of {"attributes": ..., "geometry": ...} in WGS84."""
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": 4326,
        "f": "json",
    }
    if geometry is not None:
        params.update(_geom_params(geometry, distance_m))
    if max_records:
        params["resultRecordCount"] = max_records
    # POST: parcel polygons easily exceed the server's URL length limit.
    data = get_json(layer_url + "/query", params, post=True)
    feats = data.get("features", [])
    # Past the server's record limit: page through the rest (unless the caller capped it).
    pages = 0
    while data.get("exceededTransferLimit") and not max_records and pages < MAX_PAGES:
        pages += 1
        try:
            data = get_json(layer_url + "/query", dict(params, resultOffset=len(feats)), post=True)
        except HttpError:
            break  # layer doesn't support paging: keep what we have
        more = data.get("features", [])
        if not more:
            break
        feats += more
    return feats


def simplify_polygon(geom, max_points=400):
    """Thin very detailed rings to keep requests and distance math small."""
    rings = geom["rings"]
    total = sum(len(r) for r in rings)
    if total <= max_points:
        return geom
    step = max(1, total // max_points + 1)
    out = []
    for r in rings:
        thin = r[::step]
        if thin[-1] != r[-1]:
            thin.append(r[-1])
        if len(thin) >= 4:
            out.append(thin)
    return {"rings": out or rings, "spatialReference": {"wkid": 4326}}
