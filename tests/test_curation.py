from pipeline.curation import apply_curation, approved_community_entries
from pipeline.db import (
    get_connection, init_db, upsert_brand, upsert_edge, upsert_venue, fetch_venues_with_brands,
    insert_submission, set_submission_status,
)
from pipeline.models import Venue


def _seed():
    conn = get_connection(":memory:")
    init_db(conn)
    # WALD exists in OSM with a stale finder link to Pilsner Urquell (tank).
    wid = upsert_venue(conn, Venue("node/5", "WALD", 53.5524, 9.9785), "2026-06-24")
    pu = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, wid, pu, "finder:Pilsner Urquell", "2026-06-24", serving="tank")
    return conn


def _brands(conn, name):
    rows = fetch_venues_with_brands(conn)
    return {r["name"]: r["brands"] for r in rows}[name]


def test_curation_removes_stale_adds_manual_and_creates_venue():
    conn = _seed()
    osm_venues = [Venue("node/5", "WALD", 53.5524, 9.9785)]
    entries = [
        {"venue": "WALD", "brand": "Pilsner Urquell", "action": "remove", "verified": "2026-06-24"},
        {"venue": "WALD", "brand": "budvar", "serving": "tank", "action": "add", "verified": "2026-06-24"},
        {"venue": "Pampa", "lat": 53.5556, "lon": 9.9636, "brand": "Pilsner Urquell",
         "serving": "tank", "action": "add", "verified": "2026-06-24"},
    ]
    counts = apply_curation(conn, entries, osm_venues, "2026-06-24")
    assert counts == {"added": 2, "removed": 1, "skipped": 0}

    wald = _brands(conn, "WALD")
    assert wald == [{"brand": "Budweiser Budvar", "source": "manual",
                     "serving": "tank", "last_seen": "2026-06-24"}]  # PU gone, Budvar normalized
    pampa = _brands(conn, "Pampa")  # created from coords
    assert pampa[0]["brand"] == "Pilsner Urquell" and pampa[0]["source"] == "manual"


def _approved(conn, **kw):
    base = dict(kind="add", venue_osm_id="node/5", venue_name="WALD", lat=None, lon=None,
                brand="Astra", serving="fass", address=None, note=None, submitter_ip="1.1.1.1")
    base.update(kw)
    sid = insert_submission(conn, base, "2026-06-25T10:00:00")
    set_submission_status(conn, sid, "approved", "2026-06-26")
    return sid


def test_approved_community_entries_maps_add_and_remove_skips_venue_kinds():
    conn = _seed()
    _approved(conn, kind="add", brand="Astra", serving="fass")
    _approved(conn, kind="remove", brand="Holsten")
    _approved(conn, kind="close_venue", brand="")          # no curation equivalent
    _approved(conn, kind="edit_venue", brand="", address="X")
    entries = approved_community_entries(conn)
    assert entries == [
        {"osm_id": "node/5", "brand": "Astra", "serving": "fass", "action": "add",
         "verified": "2026-06-26", "note": "community-approved (WALD)"},
        {"osm_id": "node/5", "brand": "Holsten", "action": "remove",
         "verified": "2026-06-26", "note": "community-approved (WALD)"},
    ]


def test_exported_entries_round_trip_through_apply_curation():
    # Export an approved community add, then re-apply it on a fresh DB (as if the
    # original database had been lost) and confirm the edge is reconstructed.
    src = _seed()
    _approved(src, kind="add", brand="ratsherrn", serving="fass")
    entries = approved_community_entries(src)

    fresh = get_connection(":memory:")
    init_db(fresh)
    upsert_venue(fresh, Venue("node/5", "WALD", 53.5524, 9.9785), "2026-06-26")
    counts = apply_curation(fresh, entries, [], "2026-06-26")
    assert counts == {"added": 1, "removed": 0, "skipped": 0}
    assert _brands(fresh, "WALD") == [
        {"brand": "Ratsherrn", "source": "manual", "serving": "fass", "last_seen": "2026-06-26"}]
