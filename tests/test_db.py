from pipeline.db import (
    get_connection, init_db, upsert_venue, upsert_brand,
    upsert_edge, delete_edges, fetch_venues_with_brands,
    insert_submission, list_submissions, get_submission,
    set_submission_status, count_submissions_since,
    update_venue_address, set_venue_hidden,
)
from pipeline.models import Venue


def _sub(**kw):
    base = dict(kind="add", venue_osm_id="node/1", venue_name="Bar X", lat=None, lon=None,
                brand="Astra", serving="fass", note=None, submitter_ip="1.2.3.4")
    base.update(kw)
    return base


def test_submission_crud_and_status():
    conn = _conn()
    sid = insert_submission(conn, _sub(), "2026-06-24T10:00:00")
    pending = list_submissions(conn, "pending")
    assert len(pending) == 1 and pending[0]["brand"] == "Astra" and pending[0]["status"] == "pending"
    set_submission_status(conn, sid, "approved", "2026-06-24T11:00:00")
    assert get_submission(conn, sid)["status"] == "approved"
    assert list_submissions(conn, "pending") == []


def test_count_submissions_since():
    conn = _conn()
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:00:00")
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:30:00")
    insert_submission(conn, _sub(submitter_ip="8.8.8.8"), "2026-06-24T10:30:00")
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T10:15:00") == 1
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T09:00:00") == 2


def test_community_ranks_between_manual_and_finder():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    for src in ("osm", "finder:Ratsherrn", "community", "manual"):
        bid = upsert_brand(conn, f"B-{src}")
        upsert_edge(conn, vid, bid, src, "2026-06-24")
    order = [b["source"] for b in fetch_venues_with_brands(conn)[0]["brands"]]
    assert order == ["manual", "community", "finder:Ratsherrn", "osm"]


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


def test_update_venue_address_overrides_osm_value():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="Old St 1"), "2026-06-24")
    assert update_venue_address(conn, "node/1", "New Allee 2") == 1
    assert fetch_venues_with_brands(conn)[0]["address"] == "New Allee 2"
    # A re-import from OSM overwrites the address, but it can be re-applied.
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="Old St 1"), "2026-06-25")
    assert fetch_venues_with_brands(conn)[0]["address"] == "Old St 1"
    update_venue_address(conn, "node/1", "New Allee 2")
    assert fetch_venues_with_brands(conn)[0]["address"] == "New Allee 2"


def test_hidden_venue_excluded_from_export_but_survives_reimport():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Closed Bar", 53.5, 10.0), "2026-06-24")
    upsert_venue(conn, Venue("node/2", "Open Bar", 53.6, 10.1), "2026-06-24")
    assert set_venue_hidden(conn, "node/1", True) == 1
    names = [v["name"] for v in fetch_venues_with_brands(conn)]
    assert names == ["Open Bar"]
    # OSM re-import must not resurrect a hidden venue (upsert leaves hidden alone).
    upsert_venue(conn, Venue("node/1", "Closed Bar", 53.5, 10.0), "2026-06-25")
    assert [v["name"] for v in fetch_venues_with_brands(conn)] == ["Open Bar"]
    assert set_venue_hidden(conn, "node/1", False) == 1
    assert len(fetch_venues_with_brands(conn)) == 2
