import httpx
import pytest

from pipeline import config, osm_push
from pipeline.db import (
    get_connection, get_submission, init_db, insert_submission,
    list_hours_for_osm, set_submission_status, upsert_venue,
)
from pipeline.models import Venue
from pipeline.osm_push import (
    OsmApi, _rules, changeset_tags, normalize_hours, parse_osm_id, plan, push,
    read_element, same_hours, with_opening_hours,
)

NODE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
 <node id="1" visible="true" version="3" changeset="99" timestamp="2026-08-01T10:00:00Z" user="someone" uid="7" lat="53.5" lon="10.0">
  <tag k="amenity" v="pub"/>
  <tag k="name" v="Bar X"/>
  <tag k="opening_hours" v="Mo-Su 11:00-24:00"/>
 </node>
</osm>"""


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-09-01")
    return conn


def _approved(conn, hours, osm_id="node/1", created="2026-09-01T12:00:00"):
    # venue_name is the OSM id, exactly as api/app.py stores it for every kind
    # but add_venue — the venue's name has to come from elsewhere.
    sid = insert_submission(conn, dict(kind="edit_hours", venue_osm_id=osm_id,
                                       venue_name=osm_id, opening_hours=hours,
                                       brand="", serving="unknown", submitter_ip="h:x"),
                            created)
    set_submission_status(conn, sid, "approved", "2026-09-02")
    return sid


class FakeOsm:
    """A MockTransport handler playing the API for node/1: records every
    request so a test can assert what was (not) sent."""

    def __init__(self, xml=NODE_XML, get_status=200, upload_status=200):
        self.xml, self.get_status, self.upload_status = xml, get_status, upload_status
        self.calls = []
        self.last_headers = None

    def __call__(self, request):
        path = request.url.path
        self.calls.append((request.method, path, request.content.decode()))
        self.last_headers = request.headers
        if request.method == "GET" and path == "/api/0.6/node/1":
            return httpx.Response(self.get_status, text=self.xml if self.get_status == 200 else "")
        if request.method == "PUT" and path == "/api/0.6/changeset/create":
            return httpx.Response(200, text="123\n")
        if request.method == "PUT" and path == "/api/0.6/node/1":
            ok = self.upload_status == 200
            return httpx.Response(self.upload_status, text="4" if ok else "Version mismatch")
        if request.method == "PUT" and path == "/api/0.6/changeset/123/close":
            return httpx.Response(200, text="")
        return httpx.Response(404, text="")

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method in (None, m)]


def _api(fake, token="tok"):
    return OsmApi("https://osm.test", token=token, transport=httpx.MockTransport(fake))


def test_parse_osm_id():
    assert parse_osm_id("node/373451004") == ("node", 373451004)
    assert parse_osm_id("way/12") == ("way", 12)
    assert parse_osm_id("relation/7") == ("relation", 7)
    for bad in ("community/bar-y", "manual/x", "node/", "node/abc", "", None):
        assert parse_osm_id(bad) is None


def test_changeset_tags_name_the_real_provenance():
    tags = changeset_tags("Bar  X")
    assert "zapfkompass.de" in tags["source"] and "zapfkompass.de" in tags["comment"]
    assert "Bar X" in tags["comment"]
    assert "survey" not in tags["source"]  # nobody read the sign on the door
    assert tags["created_by"].startswith("Zapfkompass")
    # Tag values are capped at 255 on the server; a long name must not push
    # the comment over.
    assert all(len(v) <= 255 for v in changeset_tags("N" * 500).values())
    assert "a venue" in changeset_tags("")["comment"]


def test_with_opening_hours_replaces_the_tag_and_sends_the_rest_back_whole():
    el = read_element(NODE_XML, "node")
    body = with_opening_hours(el, "Mo-Su 12:00-22:00", 77)
    node = read_element(body, "node")
    assert node.get("id") == "1" and node.get("version") == "3"
    assert node.get("changeset") == "77"
    assert node.get("lat") == "53.5" and node.get("lon") == "10.0"
    for gone in ("timestamp", "user", "uid", "visible"):
        assert node.get(gone) is None
    tags = {t.get("k"): t.get("v") for t in node.findall("tag")}
    assert tags == {"amenity": "pub", "name": "Bar X", "opening_hours": "Mo-Su 12:00-22:00"}
    # The fetched element is not mutated: a retry starts from what OSM sent.
    assert osm_push.tag_value(el, "opening_hours") == "Mo-Su 11:00-24:00"


def test_with_opening_hours_adds_the_tag_when_the_element_has_none():
    xml = NODE_XML.replace('  <tag k="opening_hours" v="Mo-Su 11:00-24:00"/>\n', "")
    node = read_element(with_opening_hours(read_element(xml, "node"), "24/7", 5), "node")
    assert osm_push.tag_value(node, "opening_hours") == "24/7"
    assert osm_push.tag_value(node, "name") == "Bar X"


def test_normalize_hours_spots_equivalent_spellings():
    assert same_hours("11:00-24:00", "Mo-Su 11:00-24:00")
    assert same_hours("Mo-Su 11:00-00:00", "Mo-Su 11:00-24:00")
    assert same_hours("24/7", "Mo-Su 00:00-24:00")
    assert same_hours("Mo-Fr 10:00-22:00; Sa,Su 12:00-20:00",
                      "Mo,Tu,We,Th,Fr 10:00-22:00; Sa 12:00-20:00; Su 12:00-20:00")
    assert same_hours("Mo-Fr 10:00-22:00; Sa off", "Mo-Fr 10:00-22:00; Sa closed")
    assert same_hours("Mo-Fr 10:00-22:00; Sa off", "Mo-Fr 10:00-22:00")  # unnamed days are closed
    assert not same_hours("Mo-Fr 10:00-22:00; Sa 10:00-12:00", "Mo-Fr 10:00-22:00")
    assert not same_hours("Mo-Su 11:00-24:00", "Mo-Su 11:00-23:00")
    # Sa-Mo wraps the week.
    assert normalize_hours("Sa-Mo 10:00-12:00") == normalize_hours("Sa,Su,Mo 10:00-12:00")
    # Beyond the subset: unreadable, so never "the same" — the upload goes ahead.
    for beyond in ("Mo-Fr 10:00-22:00; PH off", "sunrise-sunset", "Mo 25:00-26:00", "", None):
        assert normalize_hours(beyond) is None
    assert not same_hours(None, "Mo-Su 11:00-24:00")
    assert not same_hours("Mo-Fr 10:00-22:00; PH off", "Mo-Fr 10:00-22:00")
    assert same_hours("Mo-Fr 10:00-22:00; PH off", "Mo-Fr 10:00-22:00; PH off")  # literal equality


def test_normalize_hours_reads_spaced_lists_and_commas_used_as_semicolons():
    # The first live report (2026-09-03): OSM's own spelling put spaces after
    # the commas, and the reader saw a rule it "could not express".
    assert same_hours("We-Sa 11:00-00:00; Mo, Tu, Su 17:00-00:00",
                      "We-Sa 11:00-24:00; Mo,Tu,Su 17:00-24:00")
    assert not same_hours("We-Sa 11:00-00:00; Mo, Tu, Su 17:00-00:00",
                          "Mo 17:00-23:00; Tu-Th 17:00-24:00; Fr-Sa 15:00-02:00; Su 17:00-23:00")
    # A comma where the grammar wants ';' — read as web/hours.js does.
    assert normalize_hours("Mo-Th 18:00-24:00, Fr,Sa 18:00-02:00") == \
        normalize_hours("Mo-Th 18:00-24:00; Fr,Sa 18:00-02:00")
    # ...but a comma inside a time list, spaced or not, is still a list.
    two = normalize_hours("Mo-Fr 12:00-15:00, 17:30-22:00")
    assert two == normalize_hours("Mo-Fr 12:00-15:00,17:30-22:00")
    assert two[0] == ((12 * 60, 15 * 60), (17 * 60 + 30, 22 * 60))
    # Holidays in the day list stay out of scope, however they are spaced.
    assert normalize_hours("Mo-Su,PH 11:00-23:00") is None
    assert normalize_hours("Mo-Su, PH 11:00-23:00") is None
    # ',' adds a rule, ';' overrides — read as web/hours.js prefills the grid,
    # so an untouched grid compares equal to the tag it came from.
    assert normalize_hours("Mo-Su 11:00-14:00; Mo-Su 18:00-23:00") == \
        normalize_hours("Mo-Su 18:00-23:00")
    assert same_hours("Tu-Su 11:30-14:00, Tu-Sa 17:30-23:00",
                      "Mo off; Tu-Sa 11:30-14:00,17:30-23:00; Su 11:30-14:00")
    assert same_hours("Mo-Su 11:00-14:00, Mo-Su 18:00-23:00", "Mo-Su 11:00-14:00,18:00-23:00")
    assert same_hours("Mo-Fr 09:00-17:00, We 09:00-17:00", "Mo-Fr 09:00-17:00")  # restated, not doubled
    assert same_hours("Tu 11:30-14:00, Tu 14:00-18:00", "Tu 11:30-14:00,14:00-18:00")
    assert same_hours("Mo-Fr 15:00-01:00, Fr,Sa 15:00-03:00", "Mo-Th 15:00-01:00; Fr,Sa 15:00-03:00")
    assert normalize_hours("Mo-Su 11:00-23:00, Su 12:00-20:00") is None  # add or override? unread
    assert normalize_hours("Mo-Su 11:00-14:00, Su 12:00-20:00") is None
    # Closing after midnight runs past 1440, as in web/hours.js — the overlap
    # test above depends on it.
    assert normalize_hours("Mo 17:00-01:00")[0] == ((1020, 1500),)
    assert not same_hours("Mo 17:00-01:00", "Mo 17:00-23:00")
    # Overlap inside one rule's own list is not policed, same as the frontend.
    assert same_hours("Fr 11:00-14:00,10:00-21:00", "Fr 10:00-21:00,11:00-14:00")
    assert same_hours("Mo-Su 11:00-23:00; Su 12:00-20:00", "Mo-Sa 11:00-23:00; Su 12:00-20:00")
    assert same_hours("Mo-Su 10:00-20:00, Su off", "Mo-Sa 10:00-20:00")
    assert same_hours("Mo-Su 10:00-20:00; Su off", "Mo-Sa 10:00-20:00")
    # The splitter needs no tidying first: a raw tag splits the same way.
    assert _rules("Su-Th 18:00-01:00, Fr,Sa 18:00-02:00; Mo off") == \
        [["Su-Th 18:00-01:00", "Fr,Sa 18:00-02:00"], ["Mo off"]]


def test_push_uploads_one_changeset_per_venue():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm()
    lines = []
    counts = push(conn, _api(fake), log=lines.append, pause_s=0)

    assert counts["uploaded"] == 1 and sum(counts.values()) == 1
    assert [(m, p) for m, p, _ in fake.calls] == [
        ("GET", "/api/0.6/node/1"),
        ("PUT", "/api/0.6/changeset/create"),
        ("PUT", "/api/0.6/node/1"),
        ("PUT", "/api/0.6/changeset/123/close"),
    ]
    create_body = fake.calls[1][2]
    assert 'k="source" v="zapfkompass.de visitor report"' in create_body
    assert "opening_hours of Bar X:" in create_body and "node/1" not in create_body
    upload_body = fake.calls[2][2]
    assert 'changeset="123"' in upload_body and 'version="3"' in upload_body
    assert 'v="Mo-Su 12:00-22:00"' in upload_body and 'user=' not in upload_body
    assert fake.last_headers["Authorization"] == "Bearer tok"
    assert fake.last_headers["User-Agent"] == config.USER_AGENT
    assert get_submission(conn, sid)["osm_changeset"] == 123
    assert list_hours_for_osm(conn) == []
    assert any("changeset/123" in ln for ln in lines)


def test_changeset_comment_falls_back_to_our_venue_row_when_osm_has_no_name():
    conn = _conn()
    _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm(xml=NODE_XML.replace('  <tag k="name" v="Bar X"/>\n', ""))
    push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert "opening_hours of Bar X:" in fake.calls[1][2]


def test_a_tag_with_rules_the_grid_cannot_express_is_not_replaced_unless_forced():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 11:00-23:00")
    fake = FakeOsm(xml=NODE_XML.replace('v="Mo-Su 11:00-24:00"', 'v="Mo-Su 10:00-22:00; PH off; Dec 24 off"'))
    lines = []
    counts = push(conn, _api(fake), log=lines.append, pause_s=0)
    assert counts["conflict"] == 1 and fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] is None
    assert "cannot express" in lines[-1] and f"--force --id {sid}" in lines[-1]

    counts = push(conn, _api(fake), force=True, log=lambda s: None, pause_s=0)
    assert counts["uploaded"] == 1
    assert 'v="Mo-Su 11:00-23:00"' in fake.calls[-2][2]


def test_a_spaced_weekday_list_on_osm_is_plain_weekday_rules_not_a_conflict():
    conn = _conn()
    target = "Mo 17:00-23:00; Tu-Th 17:00-24:00; Fr-Sa 15:00-02:00; Su 17:00-23:00"
    sid = _approved(conn, target)
    fake = FakeOsm(xml=NODE_XML.replace('v="Mo-Su 11:00-24:00"',
                                        'v="We-Sa 11:00-00:00; Mo, Tu, Su 17:00-00:00"'))
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["uploaded"] == 1 and counts["conflict"] == 0
    assert f'v="{target}"' in fake.calls[-2][2]
    assert get_submission(conn, sid)["osm_changeset"] == 123


def test_an_untouched_grid_over_a_comma_added_rule_is_no_edit():
    # web/hours.js prefills the grid with both rules' hours; saved untouched,
    # it comes back as the same hours — nothing to upload, nothing lost.
    conn = _conn()
    sid = _approved(conn, "Mo off; Tu-Sa 11:30-14:00,17:30-23:00; Su 11:30-14:00")
    fake = FakeOsm(xml=NODE_XML.replace('v="Mo-Su 11:00-24:00"',
                                        'v="Tu-Su 11:30-14:00, Tu-Sa 17:30-23:00"'))
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["unchanged"] == 1 and fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] == 0


def test_a_200_with_an_unreadable_body_is_a_failure_not_a_crash():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm(xml="<html>maintenance</html>")
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["failed"] == 1 and fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] is None


def test_an_unreadable_upload_answer_still_closes_the_changeset():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm()
    real = fake.__call__

    def handler(request):
        if request.method == "PUT" and request.url.path == "/api/0.6/node/1":
            fake.calls.append((request.method, request.url.path, request.content.decode()))
            return httpx.Response(200, text="<html>not a version</html>")
        return real(request)

    api = OsmApi("https://osm.test", token="t", transport=httpx.MockTransport(handler))
    counts = push(conn, api, log=lambda s: None, pause_s=0)
    assert counts["failed"] == 1
    assert fake.paths("PUT")[-1] == "/api/0.6/changeset/123/close"
    assert get_submission(conn, sid)["osm_changeset"] is None


def test_an_interrupt_during_the_upload_closes_the_changeset():
    conn = _conn()
    _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm()
    real = fake.__call__

    def handler(request):
        if request.method == "PUT" and request.url.path == "/api/0.6/node/1":
            raise KeyboardInterrupt
        return real(request)

    api = OsmApi("https://osm.test", token="t", transport=httpx.MockTransport(handler))
    with pytest.raises(KeyboardInterrupt):
        push(conn, api, log=lambda s: None, pause_s=0)
    assert fake.paths("PUT")[-1] == "/api/0.6/changeset/123/close"


def test_dry_run_only_reads():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm()
    counts = push(conn, _api(fake, token=""), dry_run=True, log=lambda s: None, pause_s=0)
    assert counts["uploaded"] == 1
    assert fake.paths() == ["/api/0.6/node/1"]
    assert "Authorization" not in fake.last_headers
    assert get_submission(conn, sid)["osm_changeset"] is None


def test_hours_osm_already_has_are_resolved_without_an_upload():
    conn = _conn()
    sid = _approved(conn, "11:00-24:00")  # same hours as the tag, spelled differently
    fake = FakeOsm()
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["unchanged"] == 1
    assert fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] == 0


def test_an_element_edited_after_the_report_is_left_alone_unless_forced():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00", created="2026-09-01T12:00:00")
    fake = FakeOsm(xml=NODE_XML.replace("2026-08-01T10:00:00Z", "2026-09-02T09:00:00Z"))
    lines = []
    counts = push(conn, _api(fake), log=lines.append, pause_s=0)
    assert counts["conflict"] == 1 and fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] is None
    assert f"--force --id {sid}" in lines[-1] and f"--drop {sid}" in lines[-1]

    counts = push(conn, _api(fake), force=True, log=lambda s: None, pause_s=0)
    assert counts["uploaded"] == 1
    assert get_submission(conn, sid)["osm_changeset"] == 123


def test_only_the_newest_report_per_venue_is_uploaded():
    conn = _conn()
    old = _approved(conn, "Mo-Su 12:00-22:00", created="2026-09-01T12:00:00")
    new = _approved(conn, "Mo-Su 13:00-23:00", created="2026-09-01T13:00:00")
    fake = FakeOsm()
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["superseded"] == 1 and counts["uploaded"] == 1
    assert fake.paths("PUT").count("/api/0.6/changeset/create") == 1
    assert 'v="Mo-Su 13:00-23:00"' in fake.calls[2][2]
    assert get_submission(conn, old)["osm_changeset"] == 0
    assert get_submission(conn, new)["osm_changeset"] == 123


def test_an_older_report_approved_after_a_newer_one_was_pushed_is_superseded():
    conn = _conn()
    old = _approved(conn, "Mo-Su 12:00-22:00", created="2026-09-01T12:00:00")
    new = _approved(conn, "Mo-Su 13:00-23:00", created="2026-09-01T13:00:00")
    osm_push.set_submission_changeset(conn, new, 500)  # B went up first
    fake = FakeOsm()
    lines = []
    counts = push(conn, _api(fake), log=lines.append, pause_s=0)
    assert counts["superseded"] == 1 and fake.calls == []
    assert get_submission(conn, old)["osm_changeset"] == 0
    assert f"#{new}" in lines[-1] and "changeset/500" in lines[-1]


def test_our_own_previous_upload_is_not_mistaken_for_someone_elses_edit():
    conn = _conn()
    old = _approved(conn, "Mo-Su 11:00-24:00", created="2026-09-01T10:00:00")
    osm_push.set_submission_changeset(conn, old, 500)  # what OSM holds now is ours
    new = _approved(conn, "Mo-Su 12:00-22:00", created="2026-09-01T11:00:00")
    # The element's timestamp is our push, after the newer report was filed.
    fake = FakeOsm(xml=NODE_XML.replace("2026-08-01T10:00:00Z", "2026-09-01T12:00:00Z"))
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["uploaded"] == 1 and counts["conflict"] == 0
    assert get_submission(conn, new)["osm_changeset"] == 123


def test_plan_keeps_order_and_splits_by_venue():
    subs = [dict(id=1, venue_osm_id="node/1"), dict(id=2, venue_osm_id="node/2"),
            dict(id=3, venue_osm_id="node/1")]
    todo, superseded = plan(subs)
    assert [s["id"] for s in todo] == [2, 3]
    assert [s["id"] for s in superseded] == [1]


def test_an_element_gone_from_osm_is_resolved():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm(get_status=410)
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["skipped"] == 1 and fake.paths("PUT") == []
    assert get_submission(conn, sid)["osm_changeset"] == 0


def test_a_server_error_on_fetch_is_a_failure_not_a_resolution():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    counts = push(conn, _api(FakeOsm(get_status=503)), log=lambda s: None, pause_s=0)
    assert counts["failed"] == 1
    assert get_submission(conn, sid)["osm_changeset"] is None


def test_a_version_conflict_closes_the_changeset_and_keeps_the_report_pending():
    conn = _conn()
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    fake = FakeOsm(upload_status=409)
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["conflict"] == 1
    assert fake.paths("PUT")[-1] == "/api/0.6/changeset/123/close"
    assert get_submission(conn, sid)["osm_changeset"] is None


def test_a_venue_without_an_osm_element_needs_no_network():
    conn = _conn()
    upsert_venue(conn, Venue("community/bar-y", "Bar Y", 53.5, 10.0), "2026-09-01")
    sid = _approved(conn, "Mo-Su 12:00-22:00", osm_id="community/bar-y")
    fake = FakeOsm()
    counts = push(conn, _api(fake), log=lambda s: None, pause_s=0)
    assert counts["skipped"] == 1 and fake.calls == []
    assert get_submission(conn, sid)["osm_changeset"] == 0


def test_list_hours_for_osm_is_only_approved_unpushed_edit_hours():
    conn = _conn()
    base = dict(venue_osm_id="node/1", venue_name="Bar X", brand="", serving="unknown",
                submitter_ip="h:x")
    pending = insert_submission(conn, dict(base, kind="edit_hours", opening_hours="24/7"),
                                "2026-09-01T10:00:00")
    other = insert_submission(conn, dict(base, kind="edit_venue", address="Weg 1"),
                              "2026-09-01T10:00:00")
    set_submission_status(conn, other, "approved", "2026-09-02")
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    assert [s["id"] for s in list_hours_for_osm(conn)] == [sid]
    osm_push.set_submission_changeset(conn, sid, 5)
    assert list_hours_for_osm(conn) == []
    assert get_submission(conn, pending)["status"] == "pending"


def test_migration_adds_osm_changeset_to_an_older_db():
    conn = get_connection(":memory:")
    conn.execute("""CREATE TABLE submissions (
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, venue_osm_id TEXT,
        venue_name TEXT NOT NULL, lat REAL, lon REAL, brand TEXT NOT NULL DEFAULT '',
        serving TEXT NOT NULL DEFAULT 'unknown', note TEXT, submitter_ip TEXT,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, decided_at TEXT)""")
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)")}
    assert "osm_changeset" in cols


def test_cli_dry_run_needs_no_token_and_drop_needs_no_network(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "t.sqlite")
    conn = get_connection(db)
    init_db(conn)
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-09-01")
    sid = _approved(conn, "Mo-Su 12:00-22:00")
    conn.close()
    fake = FakeOsm()
    monkeypatch.setattr(osm_push, "OsmApi", lambda token="": _api(fake, token))
    monkeypatch.setattr(config, "OSM_TOKEN", "")

    assert osm_push.main(["--dry-run", "--db", db]) == 0
    assert "would upload 1" in capsys.readouterr().out
    assert fake.paths("PUT") == []

    assert osm_push.main(["--db", db]) == 2  # no token, not a dry run
    assert "OSM_TOKEN" in capsys.readouterr().err

    assert osm_push.main(["--dry-run", "--drop", str(sid), "--db", db]) == 0
    assert "would be dropped" in capsys.readouterr().out
    conn = get_connection(db)
    assert get_submission(conn, sid)["osm_changeset"] is None  # a rehearsal drops nothing
    conn.close()

    assert osm_push.main(["--drop", str(sid), "--db", db]) == 0
    assert fake.paths("PUT") == []
    conn = get_connection(db)
    assert get_submission(conn, sid)["osm_changeset"] == 0
    assert osm_push.main(["--dry-run", "--id", str(sid), "--db", db]) == 1
    assert "already resolved" in capsys.readouterr().out


def test_cli_force_requires_id(tmp_path):
    with pytest.raises(SystemExit):
        osm_push.main(["--force", "--db", str(tmp_path / "t.sqlite")])
