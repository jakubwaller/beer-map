from __future__ import annotations

import json
from pathlib import Path

from .db import fetch_venues_with_brands

GRAY_BASENAME = "venues-gray.json"


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
    # Branded venues go to out_path; the brandless rest to a sibling gray file.
    # The split keeps the file the frontend blocks its first paint on ~12x
    # smaller than the full dataset; gray dots stream in after. Gray features
    # carry no `brands` key — the frontend defaults it, and an empty list on
    # ~38k features is half a megabyte of '"brands": []'.
    rows = fetch_venues_with_brands(conn)
    branded = [_feature(r, True) for r in rows if r["brands"]]
    gray = [_feature(r, False) for r in rows if not r["brands"]]
    path = Path(out_path)
    _write(path, branded)
    _write(path.with_name(GRAY_BASENAME), gray)
    return len(branded) + len(gray)
