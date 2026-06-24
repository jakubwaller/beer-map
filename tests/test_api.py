import json

import pytest
from fastapi.testclient import TestClient

from pipeline import config
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


def test_brands_endpoint_empty(client):
    c, _ = client
    assert c.get("/api/brands").json() == []


def test_notify_is_best_effort(monkeypatch):
    from api import notify
    monkeypatch.setattr(config, "RESEND_API_KEY", "")  # unconfigured -> no-op
    assert notify.notify_new_submission("Astra", "node/1") is None
    monkeypatch.setattr(config, "RESEND_API_KEY", "x")
    monkeypatch.setattr(config, "NOTIFY_TO", "me@example.com")
    monkeypatch.setattr(config, "NOTIFY_FROM", "bot@example.com")

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.notify_new_submission("Astra", "node/1") is None  # swallowed
