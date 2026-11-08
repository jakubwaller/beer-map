import json

from pipeline import rekey
from pipeline.db import (fetch_venues_with_brands, get_connection, init_db, insert_submission,
                         resolve_osm_id, set_submission_status, set_venue_hidden, upsert_brand,
                         upsert_edge, upsert_venue)
from pipeline.models import Venue
from pipeline.run import run_pipeline
from pipeline.submissions import apply_approved, approve_submission

TODAY = "2026-11-08"
OLD = "2026-09-17"   # the last import that saw the deleted element
M = 1 / 111_320      # one metre of latitude, in degrees


def _db():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _venue(conn, osm_id, name="Kneipe Eins", north_m=0.0, seen=TODAY):
    return upsert_venue(conn, Venue(osm_id, name, 53.5 + north_m * M, 10.0), seen)


def _edge(conn, vid, brand, source="community", serving="fass", seen=OLD):
    upsert_edge(conn, vid, upsert_brand(conn, brand), source, seen, serving=serving)


def _brands(conn, osm_id):
    return {(b["brand"], b["source"], b["serving"], b["last_seen"])
            for v in fetch_venues_with_brands(conn) if v["osm_id"] == osm_id
            for b in v["brands"]}


def _ids(conn):
    return [r[0] for r in conn.execute("SELECT osm_id FROM venues ORDER BY id")]


def test_gone_venue_moves_onto_its_survivor_with_taps_and_reports():
    conn = _db()
    old = _venue(conn, "node/10", seen=OLD)
    _venue(conn, "node/20", north_m=1)   # the survivor: imported today, brandless
    _edge(conn, old, "Pilsner Urquell")
    sid = insert_submission(conn, {"kind": "add", "venue_osm_id": "node/10", "venue_name": "node/10",
                                   "brand": "Pilsner Urquell", "serving": "fass"},
                            "2026-09-01T12:00:00")
    set_submission_status(conn, sid, "approved", "2026-09-02")
    logs = []
    assert rekey.rekey_gone(conn, TODAY, log=logs.append) == 1
    assert logs[0] == "rekey: node/10 -> node/20 (Kneipe Eins, 1 m)"
    assert _ids(conn) == ["node/20"]
    assert _brands(conn, "node/20") == {("Pilsner Urquell", "community", "fass", OLD)}
    assert conn.execute("SELECT venue_osm_id FROM submissions").fetchone()[0] == "node/20"
    assert resolve_osm_id(conn, "node/10") == "node/20"
    # The nightly re-apply lands on the survivor and brings nothing back.
    apply_approved(conn, TODAY)
    assert _ids(conn) == ["node/20"]
    assert _brands(conn, "node/20") == {("Pilsner Urquell", "community", "fass", TODAY)}


def test_merge_into_a_branded_survivor_keeps_one_row_per_brand():
    conn = _db()
    old = _venue(conn, "node/10", seen=OLD)
    new = _venue(conn, "node/20", north_m=3)
    _edge(conn, old, "Pilsner Urquell", serving="tank", seen="2026-10-20")  # the newer report
    _edge(conn, new, "Pilsner Urquell", serving="fass", seen="2026-08-01")
    _edge(conn, old, "Kozel", serving="unknown", seen="2026-10-20")         # unknown never wins
    _edge(conn, new, "Kozel", serving="fass", seen="2026-07-01")
    _edge(conn, old, "Budvar", seen="2026-09-01")
    rekey.rekey_gone(conn, TODAY, log=lambda _: None)
    rows = conn.execute(
        "SELECT vb.venue_id, b.name, vb.serving, vb.first_seen, vb.last_seen FROM venue_brand vb "
        "JOIN brands b ON b.id = vb.brand_id ORDER BY b.name").fetchall()
    assert [tuple(r) for r in rows] == [
        (new, "Budvar", "fass", "2026-09-01", "2026-09-01"),
        (new, "Kozel", "fass", "2026-07-01", "2026-10-20"),
        (new, "Pilsner Urquell", "tank", "2026-08-01", "2026-10-20"),
    ]


def test_several_or_no_survivors_leave_the_venue_alone():
    conn = _db()
    _edge(conn, _venue(conn, "node/10", seen=OLD), "Kozel")
    _venue(conn, "node/11", north_m=5)
    _venue(conn, "way/12", north_m=-8)                                   # two survivors
    _edge(conn, _venue(conn, "node/30", "Zum Testkrug", 1000, seen=OLD), "Kozel")
    _venue(conn, "node/31", "Zum Testkrug", 1060)                        # 60 m: too far
    _venue(conn, "node/32", "Zum Testkrug Zwei", 1001)                   # another name
    _venue(conn, "node/33", "Zum Testkrug", 1002, seen=OLD)              # gone itself
    _venue(conn, "manual/zum-testkrug", "Zum Testkrug", 1000)            # no OSM element
    _venue(conn, "node/34", "Zum Testkrug", 1001)
    set_venue_hidden(conn, "node/34", True)                              # closed
    _venue(conn, "node/40", "Bar Zu", 2000, seen=OLD)
    set_venue_hidden(conn, "node/40", True)                              # closed and gone
    _venue(conn, "node/41", "Bar Zu", 2001)
    before = _ids(conn)
    logs = []
    assert rekey.rekey_gone(conn, TODAY, dry_run=True, log=logs.append) == 0
    assert rekey.rekey_gone(conn, TODAY, log=logs.append) == 0
    assert _ids(conn) == before
    assert "rekey: left node/10 (Kneipe Eins), several survivors: node/11, way/12" in logs
    assert "rekey: left node/30 (Zum Testkrug), no survivor" in logs     # listed on the dry run
    assert logs[-1] == "rekey: 0 re-keyed (0 with brands), 1 with several survivors, 2 gone without one"
    assert resolve_osm_id(conn, "node/10") == "node/10"


def test_way_and_node_swap_places():
    conn = _db()
    _edge(conn, _venue(conn, "way/40", "Hospůdka U Testu", seen=OLD), "Kozel", source="manual")
    _venue(conn, "node/41", "Hospudka u testu", north_m=2)      # the pub moved onto a node
    _edge(conn, _venue(conn, "node/50", "Café Test", 500, seen=OLD), "Astra")
    _venue(conn, "way/51", "Cafe Test", 540)                    # now the building, centre 40 m off
    assert rekey.rekey_gone(conn, TODAY, log=lambda _: None) == 2
    assert _ids(conn) == ["node/41", "way/51"]
    assert _brands(conn, "node/41") == {("Kozel", "manual", "fass", OLD)}
    assert _brands(conn, "way/51") == {("Astra", "community", "fass", OLD)}


def test_linked_add_venue_and_earlier_rekeys_follow_the_survivor(tmp_path):
    conn = _db()
    _venue(conn, "node/10", seen=OLD)
    _venue(conn, "node/20", north_m=1)
    conn.execute("INSERT INTO osm_rekeys VALUES ('node/5', 'node/10', '2026-10-01')")
    sid = insert_submission(conn, {"kind": "add_venue", "venue_osm_id": "", "venue_name": "Kneipe Eins",
                                   "lat": 53.5, "lon": 10.0, "brand": "Astra", "serving": "fass"},
                            "2026-11-01T10:00:00")
    conn.execute("UPDATE submissions SET assigned_osm_id='node/10' WHERE id=?", (sid,))  # linked in /admin
    rekey.rekey_gone(conn, TODAY, log=lambda _: None)
    assert resolve_osm_id(conn, "node/5") == "node/20"
    assert approve_submission(conn, sid, TODAY, str(tmp_path / "venues.json"))
    assert _ids(conn) == ["node/20"]                            # attached, not recreated
    assert _brands(conn, "node/20") == {("Astra", "community", "fass", TODAY)}


def test_nightly_build_exports_the_survivor_id(tmp_path):
    db, out, cur = str(tmp_path / "t.sqlite"), tmp_path / "venues.json", tmp_path / "curation.yaml"
    conn = get_connection(db)
    init_db(conn)
    _edge(conn, _venue(conn, "node/10", seen=OLD), "Pilsner Urquell")
    conn.commit()
    conn.close()
    # Written against the dead id, with coordinates that would recreate it.
    cur.write_text("- osm_id: node/10\n  venue: Kneipe Eins\n  lat: 53.5\n  lon: 10.0\n"
                   "  brand: Kozel\n  serving: tank\n  verified: 2026-08-01\n", encoding="utf-8")
    survivor = {"elements": [{"type": "node", "id": 20, "lat": 53.5 + M, "lon": 10.0,
                              "tags": {"amenity": "cafe", "name": "Kneipe Eins"}}]}
    run = dict(db_path=db, out_path=str(out), curation_path=str(cur),
               overpass_fetch=lambda: survivor, finders=[], today=TODAY)
    for rekeyed in (1, 0):  # the second build must not bring node/10 back
        assert run_pipeline(**run)["rekeyed"] == rekeyed
        feats = json.loads(out.read_text(encoding="utf-8"))["features"]
        assert [f["properties"]["osm_id"] for f in feats] == ["node/20"]
        assert {b["brand"] for b in feats[0]["properties"]["brands"]} == {"Pilsner Urquell", "Kozel"}
