from datetime import datetime, timezone
import urllib.parse

import httpx

from pipeline import osm_notes
from pipeline.db import (
    get_connection, get_submission, init_db, insert_submission, set_submission_status,
    upsert_venue,
)
from pipeline.models import Venue
from pipeline.osm_notes import note_text, post_notes
from pipeline.osm_push import OsmApi

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
NODE = """<osm><node id="5" version="2" lat="50.08" lon="14.42">
 <tag k="amenity" v="pub"/><tag k="name" v="U Zlatého tygra"/></node></osm>"""


class FakeOsm:
    def __init__(self, element_status=200):
        self.element_status = element_status
        self.posts = []

    def __call__(self, request):
        if request.method == "POST" and request.url.path == "/api/0.6/notes.json":
            self.posts.append(dict(urllib.parse.parse_qsl(request.url.query.decode())))
            return httpx.Response(200, json={"type": "Feature",
                                             "properties": {"id": 900 + len(self.posts)}})
        if request.method == "GET" and request.url.path == "/api/0.6/node/5":
            return httpx.Response(self.element_status, text=NODE)
        return httpx.Response(500)


def _api(fake):
    return OsmApi(base_url="https://osm.test", token="t", transport=httpx.MockTransport(fake))


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def _approved(conn, created="2026-10-20T12:00:00", **sub):
    sub = dict(dict(brand="", serving="unknown", submitter_ip="h:secretip",
                    note="call me, alice@example.com"), **sub)
    sid = insert_submission(conn, sub, created)
    set_submission_status(conn, sid, "approved", created)
    for k in ("lat", "lon", "assigned_osm_id"):
        if k in sub:
            conn.execute(f"UPDATE submissions SET {k}=? WHERE id=?", (sub[k], sid))
    return sid


def _add(conn, lat=50.08, lon=14.42, **kw):
    return _approved(conn, **dict(dict(kind="add_venue", venue_osm_id="", venue_name="Pivnice Nová",
                                       address="Husova 5, Praha", brand="Pilsner Urquell",
                                       beer="11°", serving="tank", lat=lat, lon=lon,
                                       assigned_osm_id="community/pivnice-nova"), **kw))


def _run(conn, fake, found=(), cc="cz", **kw):
    lines = []
    counts = post_notes(conn, _api(fake), now=NOW, find_osm=lambda *a: list(found),
                        country=lambda lat, lon: cc, log=lines.append, pause_s=0, **kw)
    return counts, "\n".join(lines)


def test_note_text_per_country_and_source():
    sub = dict(kind="add_venue", address="Husova 5", brand="Pilsner Urquell", beer="11°")
    assert note_text(sub, "cs", "Pivnice").startswith(
        "Nové místo: Pivnice, Husova 5; čepované pivo: Pilsner Urquell (11°)")
    assert "návštěvníka na https://zapfkompass.de" in note_text(sub, "cs", "Pivnice")
    de = note_text(sub, "de", "Pivnice")
    assert de.startswith("Neuer Ort: Pivnice, Husova 5; Bier vom Fass:") and "Moderator" in de
    assert note_text(sub, "en", "Pivnice").startswith("New place: Pivnice, Husova 5; draught beer:")
    assert "Dopivatel app" in note_text(dict(sub, partner="dopivatel"), "en", "Pivnice")
    closed = note_text(dict(kind="close_venue"), "cs", "U Tygra", "node/5")
    assert closed.startswith("Podle hlášení návštěvníka je podnik trvale zavřený: U Tygra (")
    assert "/node/5" in closed
    # Nothing claims a survey.
    assert "survey" not in note_text(sub, "en", "P").replace("not a survey", "")


def test_posts_one_note_without_pii_and_never_twice():
    conn = _conn()
    sid = _add(conn)
    fake = FakeOsm()
    counts, _ = _run(conn, fake)
    assert counts["posted"] == 1 and len(fake.posts) == 1
    post = fake.posts[0]
    assert (post["lat"], post["lon"]) == ("50.0800000", "14.4200000")
    assert post["text"].startswith("Nové místo: Pivnice Nová, Husova 5, Praha")
    assert "alice" not in post["text"] and "secretip" not in post["text"]
    assert get_submission(conn, sid)["osm_note_id"] == 901
    counts, _ = _run(conn, fake)
    assert counts["posted"] == 0 and len(fake.posts) == 1


def test_dry_run_posts_and_writes_nothing():
    conn = _conn()
    sid = _add(conn)
    fake = FakeOsm()
    counts, out = _run(conn, fake, dry_run=True)
    assert counts["posted"] == 1 and fake.posts == []
    assert "Nové místo" in out and get_submission(conn, sid)["osm_note_id"] is None


def test_skips_when_osm_has_the_place_now():
    conn = _conn()
    sid = _add(conn)
    fake = FakeOsm()
    counts, out = _run(conn, fake, found=[dict(osm_id="node/7", name="Pivnice Nová", dist=5)])
    assert fake.posts == [] and counts["resolved"] == 1 and "node/7" in out
    assert get_submission(conn, sid)["osm_note_id"] == 0


def test_skips_linked_old_and_duplicate_reports():
    conn = _conn()
    linked = _add(conn, assigned_osm_id="node/3")
    old = _add(conn, created="2026-01-01T00:00:00", venue_name="Stará hospoda")
    _add(conn)
    dup = _add(conn, lon=14.4201)  # same name, 7 m away: a second report of the same place
    fake = FakeOsm()
    counts, _ = _run(conn, fake)
    assert len(fake.posts) == 1
    assert counts == dict(posted=1, resolved=2, old=1, failed=0)
    assert get_submission(conn, linked)["osm_note_id"] == 0
    assert get_submission(conn, dup)["osm_note_id"] == 0
    assert get_submission(conn, old)["osm_note_id"] is None  # a wider cutoff still gets it


def test_close_venue_note_in_german_and_gone_element():
    conn = _conn()
    upsert_venue(conn, Venue("node/5", "Bar X", 50.08, 14.42), "2026-10-01")
    sid = _approved(conn, kind="close_venue", venue_osm_id="node/5", venue_name="node/5",
                    partner="dopivatel", ref="r1")
    fake = FakeOsm()
    counts, _ = _run(conn, fake, cc="de")
    assert counts["posted"] == 1
    text = fake.posts[0]["text"]
    assert text.startswith("Laut Hinweis eines Besuchers dauerhaft geschlossen: U Zlatého tygra")
    assert "App Dopivatel" in text and "r1" not in text
    assert get_submission(conn, sid)["osm_note_id"] == 901

    conn = _conn()
    upsert_venue(conn, Venue("node/5", "Bar X", 50.08, 14.42), "2026-10-01")
    sid = _approved(conn, kind="close_venue", venue_osm_id="node/5", venue_name="node/5")
    fake = FakeOsm(element_status=410)
    counts, _ = _run(conn, fake)
    assert fake.posts == [] and get_submission(conn, sid)["osm_note_id"] == 0


def test_osm_failure_leaves_it_pending():
    conn = _conn()
    sid = _add(conn)

    def down(*a):
        raise httpx.ConnectError("down")

    fake = FakeOsm()
    counts = post_notes(conn, _api(fake), now=NOW, find_osm=down, country=lambda *a: "cz",
                        log=lambda *a: None, pause_s=0)
    assert counts["failed"] == 1 and fake.posts == []
    assert get_submission(conn, sid)["osm_note_id"] is None


def test_main_needs_a_token_unless_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(osm_notes.config, "OSM_TOKEN", "")
    assert osm_notes.main(["--db", str(tmp_path / "x.db")]) == 2
