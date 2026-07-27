import json

import pytest
from fastapi.testclient import TestClient

from pipeline import config, submissions
from pipeline.db import get_connection, init_db, upsert_venue, list_submissions
from pipeline.models import Venue


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    out = tmp_path / "venues.json"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "OUT_PATH", str(out))
    monkeypatch.setattr(config, "ADMIN_PW", "secret")
    conn = get_connection(str(db))
    init_db(conn)
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    conn.commit()
    conn.close()
    from api.app import create_app
    return TestClient(create_app()), str(out)


def _submit(c, **kw):
    body = dict(venue_osm_id="node/1", brand="Astra", serving="fass", kind="add")
    body.update(kw)
    return c.post("/api/submit", json=body)


def test_submit_valid_inserts_pending(client):
    c, _ = client
    assert _submit(c).json() == {"ok": True}
    assert len(list_submissions(get_connection(config.DB_PATH), "pending")) == 1


def test_honeypot_silently_dropped(client):
    c, _ = client
    assert _submit(c, hp="i-am-a-bot").json() == {"ok": True}
    assert list_submissions(get_connection(config.DB_PATH), "pending") == []


def test_bad_serving_and_missing_venue_rejected(client):
    c, _ = client
    assert _submit(c, serving="lager").status_code == 400
    assert _submit(c, venue_osm_id="node/999").status_code == 400


def test_rate_limit_returns_429(client):
    c, _ = client
    for _ in range(config.RATE_LIMIT):
        assert _submit(c).status_code == 200
    assert _submit(c).status_code == 429


def test_admin_requires_auth_and_approve_exports(client):
    c, out = client
    _submit(c, brand="pilsner urquell", serving="tank")
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    assert c.get("/admin").status_code == 401
    page = c.get("/admin", auth=("admin", "secret"))
    assert page.status_code == 200 and "node/1" in page.text
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).json() == {"ok": True}
    fc = json.loads(open(out, encoding="utf-8").read())
    brands = fc["features"][0]["properties"]["brands"]
    assert len(brands) == 1
    assert brands[0]["brand"] == "Pilsner Urquell"
    assert brands[0]["source"] == "community"
    assert brands[0]["serving"] == "tank"


def test_admin_page_shows_venue_name(client):
    c, _ = client
    _submit(c)
    page = c.get("/admin", auth=("admin", "secret"))
    assert "Bar X" in page.text          # resolved venue name, not just the id
    assert "node/1" in page.text         # osm id still there for cross-reference
    assert "approve-all" in page.text    # bulk button present when pending


def test_approve_all_pending(client):
    c, out = client
    _submit(c, brand="Astra", serving="fass")
    _submit(c, brand="Ratsherrn", serving="tank")
    assert c.post("/api/admin/approve-all").status_code == 401
    r = c.post("/api/admin/approve-all", auth=("admin", "secret"))
    assert r.json() == {"ok": True, "approved": 2}
    conn = get_connection(config.DB_PATH)
    assert list_submissions(conn, "pending") == []
    assert len(list_submissions(conn, "approved")) == 2
    fc = json.loads(open(out, encoding="utf-8").read())
    brands = {b["brand"] for b in fc["features"][0]["properties"]["brands"]}
    assert brands == {"Astra", "Ratsherrn"}


def test_remove_beer_submission_accepted(client):
    c, _ = client
    assert _submit(c, kind="remove", brand="Astra", serving="unknown").status_code == 200
    pending = list_submissions(get_connection(config.DB_PATH), "pending")
    assert len(pending) == 1 and pending[0]["kind"] == "remove"


def test_edit_venue_requires_address(client):
    c, _ = client
    assert c.post("/api/submit", json={"venue_osm_id": "node/1",
                  "kind": "edit_venue"}).status_code == 400
    ok = c.post("/api/submit", json={"venue_osm_id": "node/1", "kind": "edit_venue",
                                     "address": "Neue Straße 9"})
    assert ok.status_code == 200
    pending = list_submissions(get_connection(config.DB_PATH), "pending")
    assert pending[0]["kind"] == "edit_venue" and pending[0]["address"] == "Neue Straße 9"


def test_close_venue_approval_hides_from_export(client):
    c, out = client
    assert c.post("/api/submit", json={"venue_osm_id": "node/1",
                  "kind": "close_venue"}).status_code == 200
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).json() == {"ok": True}
    fc = json.loads(open(out, encoding="utf-8").read())
    assert fc["features"] == []


def test_brands_endpoint_empty(client):
    c, _ = client
    assert c.get("/api/brands").json() == []


def test_brands_endpoint_only_lists_brands_with_venues(client):
    from pipeline.db import upsert_brand, upsert_edge, set_venue_hidden
    c, _ = client
    conn = get_connection(config.DB_PATH)
    upsert_venue(conn, Venue("node/2", "Hidden Bar", 53.6, 10.1), "2026-07-19")
    set_venue_hidden(conn, "node/2", True)
    vid = conn.execute("SELECT id FROM venues WHERE osm_id='node/1'").fetchone()["id"]
    hid = conn.execute("SELECT id FROM venues WHERE osm_id='node/2'").fetchone()["id"]
    upsert_edge(conn, vid, upsert_brand(conn, "Astra"), "osm", "2026-07-19")
    upsert_edge(conn, hid, upsert_brand(conn, "Ghost Bräu"), "osm", "2026-07-19")
    upsert_brand(conn, "Orphan Bräu")  # no venue at all
    conn.commit()
    assert c.get("/api/brands").json() == ["Astra"]


def test_notify_telegram_is_best_effort(monkeypatch):
    from api import notify
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")  # unconfigured -> no-op
    assert notify.notify_new_submission("Astra", "node/1") is None
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-100123456789")

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.notify_new_submission("Astra", "node/1") is None  # swallowed


def test_rate_limit_keys_on_forwarded_ip(client):
    c, _ = client
    body = {"venue_osm_id": "node/1", "brand": "Astra", "serving": "fass", "kind": "add"}
    h1 = {"X-Forwarded-For": "9.9.9.9, 10.0.0.1"}  # first hop is the real client
    for _ in range(config.RATE_LIMIT):
        assert c.post("/api/submit", json=body, headers=h1).status_code == 200
    assert c.post("/api/submit", json=body, headers=h1).status_code == 429  # same IP blocked
    assert c.post("/api/submit", json=body,
                  headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200  # other IP ok


def test_submit_with_specific_beer_stored(client):
    c, _ = client
    assert _submit(c, brand="Augustiner", beer="Edelstoff", serving="fass").status_code == 200
    pending = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert pending["beer"] == "Edelstoff"


def test_refuses_to_start_with_db_inside_served_dir(tmp_path, monkeypatch):
    """The DB under web/ was downloadable at /data/beer-map.sqlite — never again."""
    from api.app import _assert_db_not_served, create_app

    web = tmp_path / "web"
    (web / "data").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="publicly served"):
        _assert_db_not_served(str(web / "data" / "beer-map.sqlite"), str(web))
    # ...and the app itself refuses the bad config, not just the helper.
    monkeypatch.setattr(config, "DB_PATH", str(web / "data" / "beer-map.sqlite"))
    monkeypatch.setattr(config, "WEB_DIR", str(web))
    with pytest.raises(RuntimeError):
        create_app()
    # A sibling directory is fine.
    _assert_db_not_served(str(tmp_path / "data" / "beer-map.sqlite"), str(web))


def test_submitter_ip_is_stored_hashed(client):
    c, _ = client
    ip = "9.9.9.9"
    assert c.post("/api/submit",
                  json={"venue_osm_id": "node/1", "brand": "Astra",
                        "serving": "fass", "kind": "add"},
                  headers={"X-Forwarded-For": ip}).status_code == 200
    stored = list_submissions(get_connection(config.DB_PATH), "pending")[0]["submitter_ip"]
    assert ip not in stored
    assert stored == submissions.hash_ip(ip)  # stable, so rate limiting still works
