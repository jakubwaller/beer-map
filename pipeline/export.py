from __future__ import annotations

import json
from pathlib import Path

from .db import fetch_venues_with_brands


def export_geojson(conn, out_path: str) -> int:
    rows = fetch_venues_with_brands(conn)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {
            "name": r["name"], "address": r["address"],
            "website": r["website"], "brands": r["brands"],
        },
    } for r in rows]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False), encoding="utf-8")
    return len(features)
