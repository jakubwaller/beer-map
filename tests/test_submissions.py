from datetime import datetime

from pipeline.db import (
    get_connection, init_db, insert_submission, get_submission,
    set_submission_status, upsert_venue, fetch_venues_with_brands,
)
from pipeline.models import Venue
from pipeline.submissions import (
    validate_submission, within_rate_limit, apply_approved,
    approve_submission, reject_submission,
)


def _venue_row(conn, osm_id):
    return conn.execute("SELECT address, hidden FROM venues WHERE osm_id=?", (osm_id,)).fetchone()


def _seed():
    conn = get_connection(":memory:")
    init_db(conn)
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    return conn


def _row(**kw):
    base = dict(kind="add", venue_osm_id="node/1", venue_name="Bar X", lat=None, lon=None,
                brand="Astra", serving="fass", note=None, submitter_ip="1.1.1.1")
    base.update(kw)
    return base


def test_validate_submission():
    ok = dict(kind="add", venue_osm_id="node/1", brand="Astra", serving="fass")
    assert validate_submission(ok) is None
    assert validate_submission({**ok, "serving": "lager"})
    assert validate_submission({**ok, "brand": ""})
    assert validate_submission({**ok, "kind": "delete"})


def test_validate_remove_ignores_serving():
    # Removing a beer should not require a valid serving type.
    assert validate_submission(dict(kind="remove", venue_osm_id="node/1",
                                    brand="Astra", serving="unknown")) is None


def test_validate_venue_kinds():
    # edit_venue needs a non-empty, bounded address; close_venue needs neither.
    assert validate_submission(dict(kind="edit_venue", venue_osm_id="node/1",
                                    address="Neue Straße 5")) is None
    assert validate_submission(dict(kind="edit_venue", venue_osm_id="node/1", address=""))
    assert validate_submission(dict(kind="edit_venue", venue_osm_id="node/1",
                                    address="x" * 201))
    assert validate_submission(dict(kind="close_venue", venue_osm_id="node/1")) is None
    assert validate_submission(dict(kind="close_venue", venue_osm_id=""))


def test_approve_edit_venue_updates_address():
    conn = _seed()
    sid = insert_submission(conn, _row(kind="edit_venue", brand="",
                                       address="Neue Allee 7"), "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", "/dev/null") is True
    assert _venue_row(conn, "node/1")["address"] == "Neue Allee 7"


def test_approve_close_venue_hides_it_from_export():
    conn = _seed()
    sid = insert_submission(conn, _row(kind="close_venue", brand=""), "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", "/dev/null") is True
    assert _venue_row(conn, "node/1")["hidden"] == 1
    assert fetch_venues_with_brands(conn) == []


def test_apply_approved_reapplies_venue_edits_after_reimport():
    # Mirrors run_pipeline: OSM re-import, then approved submissions re-applied on top.
    conn = _seed()
    for kind, extra in (("edit_venue", {"address": "Korrigiert 1"}), ("close_venue", {})):
        sid = insert_submission(conn, _row(kind=kind, brand="", **extra), "2026-06-24T10:00:00")
        set_submission_status(conn, sid, "approved", "2026-06-24")
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="OSM Addr"), "2026-06-25")
    apply_approved(conn, "2026-06-25")
    row = _venue_row(conn, "node/1")
    assert row["address"] == "Korrigiert 1" and row["hidden"] == 1


def test_rate_limit_trips_after_limit():
    conn = _seed()
    now = datetime(2026, 6, 24, 12, 0, 0)
    for _ in range(10):
        insert_submission(conn, _row(submitter_ip="1.1.1.1"), now.isoformat())
    assert within_rate_limit(conn, "1.1.1.1", now) is False
    assert within_rate_limit(conn, "2.2.2.2", now) is True


def test_approve_applies_community_edge_and_reject_does_not():
    conn = _seed()
    sid = insert_submission(conn, _row(brand="pilsner urquell", serving="tank"),
                            "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", "/dev/null") is True
    assert fetch_venues_with_brands(conn)[0]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "community",
         "serving": "tank", "beer": None, "last_seen": "2026-06-24"}]
    assert get_submission(conn, sid)["status"] == "approved"

    sid2 = insert_submission(conn, _row(brand="Jever"), "2026-06-24T10:05:00")
    assert reject_submission(conn, sid2, "2026-06-24") is True
    assert get_submission(conn, sid2)["status"] == "rejected"
    assert "Jever" not in {b["brand"] for b in fetch_venues_with_brands(conn)[0]["brands"]}


def test_apply_approved_is_idempotent():
    conn = _seed()
    sid = insert_submission(conn, _row(), "2026-06-24T10:00:00")
    set_submission_status(conn, sid, "approved", "2026-06-24")
    assert apply_approved(conn, "2026-06-24") == 1
    assert apply_approved(conn, "2026-06-24") == 1
    assert len(fetch_venues_with_brands(conn)[0]["brands"]) == 1
