from pipeline.curation import apply_curation
from pipeline.db import (
    get_connection, init_db, upsert_brand, upsert_edge, upsert_venue, fetch_venues_with_brands,
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
