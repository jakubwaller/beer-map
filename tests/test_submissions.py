import os
import tempfile
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


# A throwaway export target: these tests exercise the submission logic, not
# the export, but approve_submission always re-exports.
_OUT = os.path.join(tempfile.mkdtemp(), "venues.json")


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
    assert approve_submission(conn, sid, "2026-06-24", _OUT) is True
    assert _venue_row(conn, "node/1")["address"] == "Neue Allee 7"


def test_approve_edit_venue_geocodes_near_existing_pin(monkeypatch):
    conn = _seed()
    calls = {}

    def fake_geocode(address, near=None):
        calls["args"] = (address, near)
        return (53.51, 10.01)

    monkeypatch.setattr("pipeline.submissions.geocode_address", fake_geocode)
    sid = insert_submission(conn, _row(kind="edit_venue", brand="",
                                       address="Neue Allee 7"), "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", _OUT) is True
    # bounded to the venue's current coordinates, and the pin moved to the hit
    assert calls["args"] == ("Neue Allee 7", (53.5, 10.0))
    row = conn.execute("SELECT lat, lon FROM venues WHERE osm_id='node/1'").fetchone()
    assert (row["lat"], row["lon"]) == (53.51, 10.01)


def test_approve_close_venue_hides_it_from_export():
    conn = _seed()
    sid = insert_submission(conn, _row(kind="close_venue", brand=""), "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", _OUT) is True
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
    assert approve_submission(conn, sid, "2026-06-24", _OUT) is True
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


def test_validate_add_venue():
    ok = dict(kind="add_venue", venue_osm_id="", name="Craft Eck",
              address="Musterstraße 5, 20357 Hamburg")
    assert validate_submission(ok) is None          # no venue_osm_id needed
    assert validate_submission({**ok, "name": ""})
    assert validate_submission({**ok, "name": "x" * 121})
    assert validate_submission({**ok, "address": ""})
    assert validate_submission({**ok, "brand": "Astra"})  # brand needs a serving
    assert validate_submission({**ok, "brand": "Astra", "serving": "fass"}) is None


def test_approve_add_venue_creates_venue_and_edge(monkeypatch):
    conn = _seed()
    monkeypatch.setattr("pipeline.submissions.geocode_address",
                        lambda address, near=None: (53.5678, 9.9643))
    sid = insert_submission(conn, _row(kind="add_venue", venue_osm_id="",
                                       venue_name="Craft Eck", brand="Astra", serving="fass",
                                       address="Musterstraße 5, 20357 Hamburg"),
                            "2026-07-30T10:00:00")
    assert approve_submission(conn, sid, "2026-07-30", _OUT) is True
    row = conn.execute(
        "SELECT lat, lon, address FROM venues WHERE osm_id='community/craft-eck'").fetchone()
    assert (row["lat"], row["lon"]) == (53.5678, 9.9643)
    assert row["address"] == "Musterstraße 5, 20357 Hamburg"
    venue = [v for v in fetch_venues_with_brands(conn) if v["name"] == "Craft Eck"][0]
    assert venue["brands"] == [{"brand": "Astra", "source": "community",
                                "serving": "fass", "beer": None, "last_seen": "2026-07-30"}]
    sub = get_submission(conn, sid)  # geocode hit stored for later re-applies
    assert (sub["lat"], sub["lon"]) == (53.5678, 9.9643)


def test_approve_add_venue_stays_pending_when_geocode_fails(monkeypatch):
    conn = _seed()
    monkeypatch.setattr("pipeline.submissions.geocode_address",
                        lambda address, near=None: None)
    sid = insert_submission(conn, _row(kind="add_venue", venue_osm_id="",
                                       venue_name="Nirgendwo", brand="", address="???"),
                            "2026-07-30T10:00:00")
    assert approve_submission(conn, sid, "2026-07-30", _OUT) is False
    assert get_submission(conn, sid)["status"] == "pending"
    assert conn.execute(
        "SELECT 1 FROM venues WHERE osm_id LIKE 'community/%'").fetchone() is None


def test_apply_approved_add_venue_reuses_stored_coords(monkeypatch):
    conn = _seed()
    calls = []

    def fake_geocode(address, near=None):
        calls.append(address)
        return (53.6, 10.1)

    monkeypatch.setattr("pipeline.submissions.geocode_address", fake_geocode)
    sid = insert_submission(conn, _row(kind="add_venue", venue_osm_id="",
                                       venue_name="Craft Eck", brand="",
                                       address="Musterstraße 5"), "2026-07-30T10:00:00")
    assert approve_submission(conn, sid, "2026-07-30", _OUT) is True
    # Nightly re-apply recreates the venue from the stored coordinates without
    # asking Nominatim again — even after the venue row disappeared.
    conn.execute("DELETE FROM venues WHERE osm_id='community/craft-eck'")
    assert apply_approved(conn, "2026-07-31") == 1
    assert calls == ["Musterstraße 5"]
    assert conn.execute(
        "SELECT 1 FROM venues WHERE osm_id='community/craft-eck'").fetchone()


def test_validate_beer_length():
    ok = dict(kind="add", venue_osm_id="node/1", brand="Astra", serving="fass")
    assert validate_submission({**ok, "beer": "Urtyp"}) is None
    assert validate_submission({**ok, "beer": "x" * 81})


def test_add_and_remove_specific_beer():
    conn = _seed()  # node/1 Bar X
    for beer in ("Edelstoff", "Hell"):
        sid = insert_submission(conn, _row(kind="add", brand="Augustiner",
                                           serving="fass", beer=beer), "2026-06-27T10:00:00")
        approve_submission(conn, sid, "2026-06-27", _OUT)
    assert {b["beer"] for b in fetch_venues_with_brands(conn)[0]["brands"]} == {"Edelstoff", "Hell"}

    # remove only "Hell" — the other beer of the same brand stays
    sid = insert_submission(conn, _row(kind="remove", brand="Augustiner", beer="Hell"),
                            "2026-06-27T10:05:00")
    approve_submission(conn, sid, "2026-06-27", _OUT)
    assert {b["beer"] for b in fetch_venues_with_brands(conn)[0]["brands"]} == {"Edelstoff"}

    # remove with no beer drops the whole brand
    sid = insert_submission(conn, _row(kind="remove", brand="Augustiner"), "2026-06-27T10:06:00")
    approve_submission(conn, sid, "2026-06-27", _OUT)
    assert fetch_venues_with_brands(conn)[0]["brands"] == []
