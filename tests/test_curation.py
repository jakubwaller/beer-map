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
    assert wald == [{"brand": "Budweiser Budvar", "source": "manual", "serving": "tank",
                     "beer": None, "last_seen": "2026-06-24"}]  # PU gone, Budvar normalized
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
        {"brand": "Ratsherrn", "source": "manual", "serving": "fass",
         "beer": None, "last_seen": "2026-06-26"}]


def test_venue_only_entry_creates_gray_venue():
    conn = _seed()
    entry = {"venue": "Beyond Beer", "lat": 53.5678, "lon": 9.9643,
             "address": "Weidenallee 53-55, 20357 Hamburg",
             "website": "https://example.com/", "verified": "2026-07-30"}
    counts = apply_curation(conn, [entry], [], "2026-07-30")
    assert counts == {"added": 1, "removed": 0, "skipped": 0}
    row = conn.execute(
        "SELECT address, website FROM venues WHERE osm_id='manual/beyond-beer'").fetchone()
    assert row["address"] == "Weidenallee 53-55, 20357 Hamburg"
    assert row["website"] == "https://example.com/"
    assert _brands(conn, "Beyond Beer") == []  # gray dot, no brand edge


def test_approved_add_venue_round_trips_through_curation():
    src = _seed()
    _approved(src, kind="add_venue", venue_osm_id="", venue_name="Craft Eck",
              lat=53.6, lon=10.1, brand="astra", serving="fass",
              address="Musterstraße 5")
    entries = approved_community_entries(src)
    assert entries == [
        {"osm_id": "community/craft-eck", "venue": "Craft Eck", "lat": 53.6, "lon": 10.1,
         "address": "Musterstraße 5", "brand": "astra", "serving": "fass",
         "verified": "2026-06-26", "note": "community-approved (Craft Eck)"}]
    # As if the database had been lost: re-applying the exported entry recreates
    # the venue under its original osm_id, brand edge included.
    fresh = get_connection(":memory:")
    init_db(fresh)
    counts = apply_curation(fresh, entries, [], "2026-06-27")
    assert counts == {"added": 1, "removed": 0, "skipped": 0}
    assert _brands(fresh, "Craft Eck") == [
        {"brand": "Astra", "source": "manual", "serving": "fass",
         "beer": None, "last_seen": "2026-06-26"}]  # the entry's verified date


def test_curation_sets_specific_beer():
    conn = _seed()  # WALD = node/5
    apply_curation(conn, [{"osm_id": "node/5", "brand": "Ratsherrn", "serving": "fass",
                           "beer": "Pilsener", "action": "add"}], [], "2026-06-27")
    edge = [b for b in _brands(conn, "WALD") if b["brand"] == "Ratsherrn"][0]
    assert edge["beer"] == "Pilsener"
