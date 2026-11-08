import json

import httpx

from pipeline import dedupe, rekey
from pipeline.db import (fetch_venues_with_brands, get_connection, init_db, insert_submission,
                         resolve_osm_id, set_submission_status, set_venue_hidden, upsert_brand,
                         upsert_edge, upsert_venue)
from pipeline.models import Venue
from pipeline.osm_push import OsmApi
from pipeline.run import run_pipeline
from pipeline.submissions import apply_approved, approve_submission, link_submission

TODAY = "2026-11-08"
OLD = "2026-09-17"   # the last import that saw the deleted element
M = 1 / 111_320      # one metre of latitude, in degrees


def _osm(answers):
    """The OSM API as a stub: `answers` maps "node/10" to a status (410 = deleted)
    or to the name the element still carries ("" = none); anything else times out.
    `.asked` lists the elements asked about."""
    asked = []

    def handler(request):
        kind, oid = request.url.path.split("/")[-2:]
        asked.append(f"{kind}/{oid}")
        answer = answers.get(f"{kind}/{oid}")
        if answer is None:
            raise httpx.ReadTimeout("timed out", request=request)
        if isinstance(answer, int):
            return httpx.Response(answer)
        tag = f'<tag k="name" v="{answer}"/>' if answer else ""
        return httpx.Response(200, text=f'<osm><{kind} id="{oid}" visible="true">{tag}</{kind}></osm>')
    api = OsmApi("https://osm.test", transport=httpx.MockTransport(handler))
    api.asked = asked
    return api


DELETED = 410


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
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=_osm({"node/10": DELETED})) == 1
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
    rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=_osm({"node/10": DELETED}))
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
    logs, api = [], _osm({})
    assert rekey.rekey_gone(conn, TODAY, dry_run=True, log=logs.append, api=api) == 0
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=api) == 0
    assert _ids(conn) == before and api.asked == []
    assert "rekey: left node/10 (Kneipe Eins), several survivors: node/11, way/12" in logs
    assert "rekey: left node/30 (Zum Testkrug), no matching survivor" in logs  # dry run only
    assert logs[-1] == ("rekey: 0 re-keyed (0 with brands), 0 still in OSM, 0 not asked yet, "
                        "1 with several survivors, 2 without a matching one")
    assert resolve_osm_id(conn, "node/10") == "node/10"


def test_way_and_node_swap_places():
    conn = _db()
    _edge(conn, _venue(conn, "way/40", "Hospůdka U Testu", seen=OLD), "Kozel", source="manual")
    _venue(conn, "node/41", "Hospudka u testu", north_m=2)      # the pub moved onto a node
    _edge(conn, _venue(conn, "node/50", "Café Test", 500, seen=OLD), "Astra")
    _venue(conn, "way/51", "Cafe Test", 540)                    # now the building, centre 40 m off
    # The way still exists as a building without a name; the node is deleted.
    api = _osm({"way/40": "", "node/50": DELETED})
    assert rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=api) == 2
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
    rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=_osm({"node/10": DELETED}))
    assert resolve_osm_id(conn, "node/5") == "node/20"
    assert approve_submission(conn, sid, TODAY, str(tmp_path / "venues.json"))
    assert _ids(conn) == ["node/20"]                            # attached, not recreated
    assert _brands(conn, "node/20") == {("Astra", "community", "fass", TODAY)}


def test_nightly_build_exports_the_survivor_id(tmp_path, monkeypatch):
    monkeypatch.setattr(rekey, "OsmApi", lambda: _osm({"node/10": DELETED}))
    db, out, cur = str(tmp_path / "t.sqlite"), tmp_path / "venues.json", tmp_path / "curation.yaml"
    conn = get_connection(db)
    init_db(conn)
    _edge(conn, _venue(conn, "node/10", seen=OLD), "Pilsner Urquell")
    _venue(conn, "node/20", north_m=1, seen="2026-11-07")       # the survivor, swept yesterday
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


def test_only_an_element_osm_no_longer_has_is_merged():
    conn = _db()
    _edge(conn, _venue(conn, "node/10", seen=OLD), "Kozel")
    _venue(conn, "node/20", north_m=1)
    _edge(conn, _venue(conn, "node/30", "Zum Testkrug", 500, seen=OLD), "Astra")
    _venue(conn, "node/31", "Zum Testkrug", 501)
    # OSM still has node/10 under its name (renamed, retagged out of the sweep...).
    logs, api = [], _osm({"node/10": "Kneipe Eins", "node/30": DELETED})
    assert rekey.rekey_gone(conn, TODAY, dry_run=True, log=logs.append, api=api) == 1
    assert "rekey: kept node/10 (Kneipe Eins), OSM still has it" in logs
    assert _ids(conn) == ["node/10", "node/20", "node/30", "node/31"]   # the dry run wrote nothing
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=api) == 1
    assert _ids(conn) == ["node/10", "node/20", "node/31"]
    # Still there counts as seen: tomorrow it is not asked again.
    assert conn.execute("SELECT updated_at FROM venues WHERE osm_id='node/10'").fetchone()[0] == TODAY
    api.asked.clear()
    rekey.rekey_gone(conn, "2026-11-09", log=logs.append, api=api)
    assert api.asked == []


def test_a_failed_lookup_merges_nothing_and_stops_asking():
    conn = _db()
    _venue(conn, "node/10", seen=OLD)
    _venue(conn, "node/20", north_m=1)
    _venue(conn, "node/30", "Zum Testkrug", 500, seen=OLD)
    _venue(conn, "node/31", "Zum Testkrug", 501)
    logs, api = [], _osm({"node/30": DELETED})                  # node/10 times out
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=api) == 0
    assert api.asked == ["node/10"] and len(_ids(conn)) == 4
    assert logs[0].startswith("rekey: OSM lookup of node/10 failed (timed out); 2 left")
    api = _osm({"node/10": 503, "node/30": DELETED})              # a server error is no answer either
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=api) == 0 and len(_ids(conn)) == 4


def test_partner_hint_venue_still_in_osm_stays():
    """A partner report on an element the sweeps never import (craft=brewery),
    and a same-named pub 30 m away that every sweep refreshes."""
    conn = _db()
    sid = insert_submission(conn, {"kind": "add", "venue_osm_id": "node/99", "venue_name": "Pivovar Test",
                                   "brand": "Test Lager", "serving": "tank", "partner": "dopivatel",
                                   "ref": "r1", "hint_lat": 50.0, "hint_lon": 14.0},
                            "2026-10-01T10:00:00")
    set_submission_status(conn, sid, "approved", "2026-10-01")
    apply_approved(conn, "2026-10-01")
    api = _osm({"node/99": "Pivovar Test"})
    for day in ("2026-10-05", "2026-10-11", "2026-10-12"):
        upsert_venue(conn, Venue("node/1", "Pivovar Test", 50.0 + 30 * M, 14.0), day)   # the sweep
        apply_approved(conn, day)
        assert rekey.rekey_gone(conn, day, log=lambda _: None, api=api) == 0
    assert api.asked == ["node/99"]
    assert _brands(conn, "node/99") == {("Test Lager", "community", "tank", "2026-10-12")}
    assert resolve_osm_id(conn, "node/99") == "node/99"


def test_a_missed_weekly_sweep_merges_nothing_osm_still_has():
    """The country sweep failed; the brewery-tagged venue is fresh from the
    nightly, its same-named neighbour is only refreshed by the sweep."""
    conn = _db()
    old = upsert_venue(conn, Venue("node/2", "Brauerei Test", 49.0, 11.0), "2026-10-25")
    _edge(conn, old, "Test Bräu", seen="2026-10-20")
    upsert_venue(conn, Venue("way/3", "Brauerei Test", 49.0 + 40 * M, 11.0), "2026-11-05")
    api = _osm({"node/2": "Brauerei Test"})
    assert rekey.rekey_gone(conn, "2026-11-05", log=lambda _: None, api=api) == 0
    assert api.asked == ["node/2"] and _ids(conn) == ["node/2", "way/3"]


def test_generic_names_merge_only_close_by_and_addresses_must_agree():
    conn = _db()
    _venue(conn, "node/1", "Kiosk", 5000)                         # more Kiosks: the name is generic
    _venue(conn, "node/2", "Kiosk", 6000)
    _venue(conn, "node/10", "Kiosk", seen=OLD)
    _venue(conn, "node/11", "Kiosk", north_m=30)                 # another kiosk across the square
    _venue(conn, "node/20", "Imbiss", 1000, seen=OLD)
    _venue(conn, "node/21", "Imbiss", 1005)
    _venue(conn, "node/22", "Imbiss", 3000)
    upsert_venue(conn, Venue("node/30", "Občerstvení", 50.0, 14.0, "Hlavní 1, 10000 Praha"), OLD)
    upsert_venue(conn, Venue("node/31", "Obcerstveni", 50.0 + 5 * M, 14.0, "Hlavní 3"), TODAY)
    _venue(conn, "node/32", "Občerstvení", 4000)
    # Not generic: 50 m, unless both addresses are known and differ.
    upsert_venue(conn, Venue("node/40", "Kneipe Eins", 51.0, 12.0, "Testweg 1, 01234 Testdorf"), OLD)
    upsert_venue(conn, Venue("node/41", "Kneipe Eins", 51.0 + 40 * M, 12.0, "Testweg 7"), TODAY)
    upsert_venue(conn, Venue("node/50", "Zum Testkrug", 51.1, 12.0, "Testweg 1, 01234 Testdorf"), OLD)
    upsert_venue(conn, Venue("node/51", "Zum Testkrug", 51.1 + 40 * M, 12.0, "Testweg 1"), TODAY)
    upsert_venue(conn, Venue("node/60", "Bar Zu", 51.2, 12.0, "Testweg 2"), OLD)
    upsert_venue(conn, Venue("node/61", "Bar Zu", 51.2 + 40 * M, 12.0), TODAY)
    api = _osm({f"node/{i}": DELETED for i in (10, 20, 30, 40, 50, 60)})
    rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=api)
    assert sorted(api.asked) == ["node/20", "node/50", "node/60"]
    assert resolve_osm_id(conn, "node/20") == "node/21"          # generic, 5 m
    assert resolve_osm_id(conn, "node/50") == "node/51"          # same street
    assert resolve_osm_id(conn, "node/60") == "node/61"          # one address unknown
    for kept in ("node/10", "node/30", "node/40"):               # 30 m generic; other street twice
        assert resolve_osm_id(conn, kept) == kept


def test_cached_candidates_link_to_the_survivor(tmp_path):
    conn = _db()
    _venue(conn, "node/10", seen=OLD)
    _venue(conn, "node/20", north_m=1)
    sid = insert_submission(conn, {"kind": "add_venue", "venue_osm_id": "", "venue_name": "Kneipe Eins",
                                   "lat": 53.5, "lon": 10.0, "brand": "Astra", "serving": "fass"},
                            "2026-09-10T10:00:00")
    # /admin cached OSM's answer while node/10 still existed there.
    conn.execute("UPDATE submissions SET osm_candidates=? WHERE id=?", (json.dumps([
        {"osm_id": "node/10", "name": "Kneipe Eins", "kind": "amenity=pub",
         "lat": 53.5, "lon": 10.0, "dist": 0}]), sid))
    rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=_osm({"node/10": DELETED}))
    conn.commit()
    sub = conn.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
    assert [c["osm_id"] for c in dedupe.candidates(conn, dict(sub))[0]] == ["node/20"]
    assert link_submission(conn, sid, "node/10")                 # a page rendered before the re-key
    assert conn.execute("SELECT assigned_osm_id FROM submissions").fetchone()[0] == "node/20"
    # A row still naming the dead id is resolved on approval too.
    conn.execute("UPDATE submissions SET assigned_osm_id='node/10'")
    assert approve_submission(conn, sid, TODAY, str(tmp_path / "venues.json"))
    assert _ids(conn) == ["node/20"]
    assert _brands(conn, "node/20") == {("Astra", "community", "fass", TODAY)}


def test_partner_name_differing_from_the_live_osm_name_is_not_gone():
    """Probe A: the stored name is the partner's; OSM's live element is named
    differently and still there, so a nearby namesake must not absorb it."""
    conn = _db()
    sid = insert_submission(conn, {"kind": "add", "venue_osm_id": "node/99", "venue_name": "Pivovar Test",
                                   "brand": "Test Lager", "serving": "tank", "partner": "dopivatel",
                                   "ref": "r1", "hint_lat": 50.0, "hint_lon": 14.0},
                            "2026-10-01T10:00:00")
    set_submission_status(conn, sid, "approved", "2026-10-01")
    apply_approved(conn, "2026-10-01")
    upsert_venue(conn, Venue("node/1", "Pivovar Test", 50.0 + 30 * M, 14.0), "2026-10-12")
    api = _osm({"node/99": "Pivovar Test - varna"})
    assert rekey.rekey_gone(conn, "2026-10-12", log=lambda _: None, api=api) == 0
    assert resolve_osm_id(conn, "node/99") == "node/99"


def test_a_404_skips_the_venue_and_the_night_goes_on():
    """Probe B: a 404 is an id that never existed (wrong OSM_API_URL), not a deletion."""
    conn = _db()
    _venue(conn, "node/10", seen=OLD)
    _venue(conn, "node/20", north_m=1)
    _venue(conn, "node/30", "Zum Testkrug", 500, seen=OLD)
    _venue(conn, "node/31", "Zum Testkrug", 501)
    logs, api = [], _osm({"node/10": 404, "node/30": DELETED})
    assert rekey.rekey_gone(conn, TODAY, log=logs.append, api=api) == 1
    assert api.asked == ["node/10", "node/30"]
    assert _ids(conn) == ["node/10", "node/20", "node/31"]
    assert any("node/10 (404)" in m for m in logs)
    # Backed off like a kept venue: not merged, not asked again tonight or tomorrow.
    assert conn.execute("SELECT updated_at FROM venues WHERE osm_id='node/10'").fetchone()[0] == TODAY
    api.asked.clear()
    rekey.rekey_gone(conn, TODAY, log=logs.append, api=api)
    assert api.asked == []


def test_namesakes_are_counted_beside_the_pair_visible_and_nearby():
    """Probe C: a far-off namesake, a hidden one and the pair itself do not make a name generic."""
    conn = _db()
    _venue(conn, "way/50", "Café Central", seen=OLD)
    _venue(conn, "node/51", "Cafe Central", 40)
    for i, (north, hidden) in enumerate(((500_000, 0), (2000, 1), (3000, 1))):
        vid = _venue(conn, f"node/9{i}", "Café Central", north)
        if hidden:
            set_venue_hidden(conn, f"node/9{i}", True)
    assert rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=_osm({"way/50": DELETED})) == 1
    assert resolve_osm_id(conn, "way/50") == "node/51"
    # Two visible namesakes in town: genuinely generic, 40 m is too far.
    conn = _db()
    _venue(conn, "node/10", "Kiosk", seen=OLD)
    _venue(conn, "node/11", "Kiosk", 40)
    _venue(conn, "node/12", "Kiosk", 2000)
    _venue(conn, "node/13", "Kiosk", 3000)
    api = _osm({"node/10": DELETED})
    assert rekey.rekey_gone(conn, TODAY, log=lambda _: None, api=api) == 0 and api.asked == []
