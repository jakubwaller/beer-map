from __future__ import annotations

import json
from pathlib import Path

from .db import fetch_venues_with_brands


def _feature(r, with_brands: bool) -> dict:
    props = {
        "name": r["name"], "address": r["address"], "osm_id": r["osm_id"],
        "website": r["website"], "opening_hours": r["opening_hours"],
    }
    if with_brands:
        props["brands"] = r["brands"]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": props,
    }


def _write(path: Path, features: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False), encoding="utf-8")


def export_geojson(conn, out_path: str) -> int:
    # Only branded venues are exported: the file powers first paint, the brand
    # chips and the stats, so it must stay small. The brandless rest of the
    # dataset (~250k rows since the nationwide sweep) never touches a file —
    # the frontend fetches it per viewport from /api/gray, straight off the DB.
    branded = [_feature(r, True) for r in fetch_venues_with_brands(conn, branded_only=True)]
    _write(Path(out_path), branded)
    return len(branded)
