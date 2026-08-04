import json

from pipeline.db import get_connection, init_db, upsert_brand, upsert_edge, upsert_venue
from pipeline.export import export_geojson
from pipeline.models import Venue


def test_export_writes_geojson_with_brand_provenance(tmp_path):
    conn = get_connection(":memory:")
    init_db(conn)
    vid = upsert_venue(conn, Venue("manual/pampa", "Pampa", 53.5556, 9.9636,
                                   address="Hamburg",
                                   opening_hours="Mo-Su 12:00-23:00"), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "manual", "2026-06-24", serving="tank")

    out = tmp_path / "venues.json"
    assert export_geojson(conn, str(out)) == 1
    fc = json.loads(out.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [9.9636, 53.5556]  # lon, lat
    assert feat["properties"]["opening_hours"] == "Mo-Su 12:00-23:00"
    assert feat["properties"]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "manual", "serving": "tank",
         "beer": None, "last_seen": "2026-06-24"}
    ]
