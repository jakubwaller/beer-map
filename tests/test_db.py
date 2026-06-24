from pipeline.db import (
    get_connection, init_db, upsert_venue, upsert_brand,
    upsert_edge, delete_edges, fetch_venues_with_brands,
)
from pipeline.models import Venue


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_upsert_venue_is_idempotent_by_osm_id():
    conn = _conn()
    id1 = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    id2 = upsert_venue(conn, Venue("node/1", "Bar X (renamed)", 53.5, 10.0), "2026-06-25")
    assert id1 == id2
    rows = conn.execute("select name from venues").fetchall()
    assert len(rows) == 1 and rows[0]["name"] == "Bar X (renamed)"


def test_edge_with_serving_and_provenance_roundtrips():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "manual", "2026-06-24", serving="tank")
    out = fetch_venues_with_brands(conn)
    assert out[0]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "manual",
         "serving": "tank", "last_seen": "2026-06-24"}
    ]


def test_delete_edges_removes_stale_links():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "WALD", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "finder:Pilsner Urquell", "2026-06-24", serving="tank")
    assert delete_edges(conn, vid, bid) == 1
    assert fetch_venues_with_brands(conn)[0]["brands"] == []
