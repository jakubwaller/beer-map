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
         "serving": "tank", "last_seen": "2026-06-24"}]
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
