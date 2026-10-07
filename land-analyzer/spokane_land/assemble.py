"""A listing that covers several county parcels.

A 20-acre listing whose map pin lands on a 5-acre parcel usually sells that parcel plus its
neighbours with the same owner. Where the county publishes owner names (Spokane and Kootenai counties), collect touching parcels with the same owner until their total matches the
listed acreage, and analyse them as one piece of land. Parcels with a house or other buildings on
them are left out: an owner selling bare land next door usually keeps the home.

The statewide parcel layers used for the other counties have no owner names, so there
the listing keeps the pinned parcel and the "only part of this listing was checked" warning.
"""

import re

from . import arcgis, geo
from .config import LAYERS
from .http import HttpError

MIN_SHARE = 0.6        # pinned parcel under 60% of the listed acres: look for the rest
MATCH = (0.85, 1.2)    # assembled total must land within this band of the listed acres
MAX_PARCELS = 20
ROUNDS = 4
TOUCH_M = 12           # parcel lines in county data don't meet exactly


def _norm_owner(name):
    return re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).strip()


def _improved(use):
    use = (use or "").lower()
    return bool(use) and "vacant" not in use and any(
        w in use for w in ("residen", "dwelling", "household", "mobile", "manufactured", "commercial", "industrial", "unit"))


def _neighbours(layer, rings):
    feats = arcgis.query(LAYERS[layer], geometry={"rings": rings, "spatialReference": {"wkid": 4326}},
                         distance_m=TOUCH_M, return_geometry=True, max_records=200)
    return [f for f in feats if (f.get("geometry") or {}).get("rings")]


def _spokane_info(pids):
    """PID -> (owner, land use) from the Spokane County assessor."""
    out = {}
    pids = sorted(pids)
    for i in range(0, len(pids), 80):
        feats = arcgis.query(LAYERS["sc_property"], where="PID_NUM IN (" + ",".join(f"'{p}'" for p in pids[i:i + 80]) + ")",
                             out_fields="PID_NUM,owner_name,prop_use_desc")
        for f in feats:
            a = f["attributes"]
            out[a["PID_NUM"]] = (a.get("owner_name") or "", a.get("prop_use_desc") or "")
    return out


def assemble(listing, parcel):
    """Return {"ids": [...], "acres": total, "rings": [...]} when same-owner neighbours make up the
    listed acreage, else None. The pinned parcel is always first."""
    listed = listing.lot_acres
    if not (listed and parcel and parcel.geometry and parcel.geometry.get("rings") and parcel.owner):
        return None
    if parcel.county == "Spokane":
        layer, id_field = "sc_parcels", "PID_NUM"
    elif parcel.county == "Kootenai":
        layer, id_field = "kootenai_parcels", "PIN"
    else:
        return None
    base = parcel.acres or geo.polygon_area_acres(parcel.geometry["rings"]) or 0
    if base >= MIN_SHARE * listed:
        return None
    owner = _norm_owner(parcel.owner)
    members = {parcel.parcel_id: (parcel.geometry["rings"], base)}
    frontier = parcel.geometry["rings"]
    try:
        for _ in range(ROUNDS):
            feats = [f for f in _neighbours(layer, frontier) if f["attributes"].get(id_field) not in members]
            if not feats:
                break
            if layer == "sc_parcels":
                info = _spokane_info({f["attributes"][id_field] for f in feats})
            else:
                info = {f["attributes"][id_field]: (f["attributes"].get("Name") or "", "") for f in feats}
            new = []
            for f in feats:
                pid = f["attributes"][id_field]
                own, use = info.get(pid, ("", ""))
                if pid in members or _norm_owner(own) != owner or _improved(use):
                    continue
                rings = f["geometry"]["rings"]
                a = f["attributes"]
                members[pid] = (rings, a.get("acreage") or a.get("Acres") or geo.polygon_area_acres(rings) or 0)
                new.append(rings)
                if len(members) >= MAX_PARCELS:
                    break
            total = sum(a for _, a in members.values())
            if not new or total >= MATCH[0] * listed or len(members) >= MAX_PARCELS:
                break
            frontier = [r for rings in new for r in rings]
    except HttpError:
        return None
    total = sum(a for _, a in members.values())
    if len(members) < 2 or not (MATCH[0] * listed <= total <= MATCH[1] * listed):
        return None
    return {"ids": list(members), "acres": round(total, 2), "rings": [r for rings, _ in members.values() for r in rings]}
