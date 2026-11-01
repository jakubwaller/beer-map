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
    # Browsers send Sec-Fetch-Site on every request; admin writes require same-origin.
    return TestClient(create_app(), headers={"Sec-Fetch-Site": "same-origin"}), str(out)


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


def test_admin_write_from_another_site_is_refused(client):
    # Cached basic credentials ride along on a cross-site POST; only the admin page may write.
    c, _ = client
    _submit(c)
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    for url in (f"/api/admin/{sid}/approve", f"/api/admin/{sid}/reject", "/api/admin/approve-all"):
        r = c.post(url, auth=("admin", "secret"), headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
    assert len(list_submissions(get_connection(config.DB_PATH), "pending")) == 1
    r = c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret"),
               headers={"Sec-Fetch-Site": "same-origin"})
    assert r.json() == {"ok": True}


def test_admin_write_without_fetch_metadata_needs_own_origin(client, monkeypatch):
    # Older browsers send no Sec-Fetch-Site: then Origin or Referer must be this site.
    c, _ = client
    monkeypatch.setattr(config, "PUBLIC_URL", "https://zapfkompass.de")
    bare = TestClient(c.app)  # no default Sec-Fetch-Site
    _submit(c)
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    url, auth = f"/api/admin/{sid}/reject", ("admin", "secret")
    assert bare.post(url, auth=auth).status_code == 403
    assert bare.post(url, auth=auth, headers={"Origin": "https://evil.example"}).status_code == 403
    assert bare.post(url, auth=auth,
                     headers={"Origin": "https://zapfkompass.de.evil.example"}).status_code == 403
    assert bare.post(url, auth=auth,
                     headers={"Referer": "https://zapfkompass.de/admin"}).json() == {"ok": True}


def test_admin_write_origin_check_refuses_when_public_url_is_empty(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    assert TestClient(c.app).post("/api/admin/approve-all",
                                  auth=("admin", "secret")).status_code == 403


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


def test_gray_tile_serves_brandless_venues_with_cache_header(client):
    c, _ = client
    # Bar X (53.5, 10.0) is brandless; its z10 slippy tile is 10/540/331.
    r = c.get("/api/gray/10/540/331")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=3600"
    names = [f["properties"]["name"] for f in r.json()["features"]]
    assert names == ["Bar X"]
    assert "brands" not in r.json()["features"][0]["properties"]
    assert c.get("/api/gray/10/550/331").json()["features"] == []  # elsewhere: empty
    assert c.get("/api/gray/2/1/1").status_code == 404   # zoomed-out z: no DB dump
    assert c.get("/api/gray/10/9999/331").status_code == 404


def test_gray_tile_excludes_venues_that_gained_a_brand(client):
    from pipeline.db import upsert_brand, upsert_edge
    c, _ = client
    conn = get_connection(config.DB_PATH)
    vid = conn.execute("SELECT id FROM venues WHERE osm_id='node/1'").fetchone()["id"]
    upsert_edge(conn, vid, upsert_brand(conn, "Astra"), "osm", "2026-08-11")
    conn.commit()
    assert c.get("/api/gray/10/540/331").json()["features"] == []


def test_search_endpoint_folds_and_includes_brands(client):
    from pipeline.db import upsert_brand, upsert_edge
    c, _ = client
    conn = get_connection(config.DB_PATH)
    vid = upsert_venue(conn, Venue("node/7", "Zum Goldenen Handwerk", 51.0, 13.7,
                                   address="Dresden"), "2026-08-11")
    upsert_edge(conn, vid, upsert_brand(conn, "Radeberger"), "osm", "2026-08-11")
    conn.commit()
    feats = c.get("/api/search", params={"q": "göldenen handwerk"}).json()["features"]
    assert [f["properties"]["name"] for f in feats] == ["Zum Goldenen Handwerk"]
    assert feats[0]["properties"]["brands"][0]["brand"] == "Radeberger"
    assert c.get("/api/search", params={"q": ""}).json()["features"] == []


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


def test_add_venue_submission_and_approval(client, monkeypatch):
    c, out = client
    r = c.post("/api/submit", json={"kind": "add_venue", "name": "Craft Eck",
                                    "address": "Musterstraße 5, 20357 Hamburg"})
    assert r.status_code == 200
    pending = list_submissions(get_connection(config.DB_PATH), "pending")
    assert pending[0]["kind"] == "add_venue" and pending[0]["venue_name"] == "Craft Eck"
    page = c.get("/admin", auth=("admin", "secret"))
    assert "Craft Eck" in page.text and "Neuer Ort" in page.text
    monkeypatch.setattr(submissions, "geocode_address",
                        lambda address, near=None: (53.57, 9.96))
    sid = pending[0]["id"]
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).json() == {"ok": True}
    # Brandless, so it stays out of the branded export and surfaces as a gray
    # dot through the tile API instead.
    fc = json.loads(open(out, encoding="utf-8").read())
    assert all(f["properties"]["name"] != "Craft Eck" for f in fc["features"])
    tile = c.get("/api/gray/10/540/330").json()   # the z10 tile holding (53.57, 9.96)
    craft = [f for f in tile["features"] if f["properties"]["name"] == "Craft Eck"][0]
    assert "brands" not in craft["properties"]
    assert craft["geometry"]["coordinates"] == [9.96, 53.57]


def test_add_venue_requires_name_and_address(client):
    c, _ = client
    assert c.post("/api/submit", json={"kind": "add_venue",
                  "address": "Musterstraße 5"}).status_code == 400
    assert c.post("/api/submit", json={"kind": "add_venue",
                  "name": "Craft Eck"}).status_code == 400


def test_add_venue_ungecodable_approval_stays_pending(client, monkeypatch):
    c, _ = client
    c.post("/api/submit", json={"kind": "add_venue", "name": "Nirgendwo",
                                "address": "keine echte Adresse"})
    monkeypatch.setattr(submissions, "geocode_address", lambda address, near=None: None)
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).status_code == 404
    assert list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"] == sid


def test_index_localizes_social_meta_for_lang_param(client):
    c, _ = client
    de = c.get("/").text
    assert 'og:locale" content="de_DE"' in de and '<html lang="de">' in de

    cs = c.get("/?lang=cs").text
    assert '<html lang="cs">' in cs
    assert "<title>Zapfkompass – pivo ze sudu a z tanku</title>" in cs
    assert 'og:locale" content="cs_CZ"' in cs
    assert 'og:url" content="https://zapfkompass.de/?lang=cs"' in cs
    assert "hospodu po hospodě" in cs
    # The shared image and its dimensions stay: they are language-neutral.
    assert 'og:image" content="https://zapfkompass.de/og-image.jpg"' in cs

    # Junk (or German) serves the file untouched.
    assert c.get("/?lang=xx").text == de


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

def test_admin_login_link_sets_cookie_and_authenticates(client):
    from datetime import datetime

    from api import admin_links
    c, _ = client
    token = admin_links.make_token(datetime.now())
    r = c.get(f"/admin/login?key={token}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin"
    assert "beermap_admin" in r.headers["set-cookie"]
    assert "HttpOnly" in r.headers["set-cookie"]
    # The cookie is Secure, so the TestClient's http:// jar won't replay it —
    # send it by hand.
    assert c.get("/admin",
                 headers={"Cookie": f"beermap_admin={token}"}).status_code == 200
    assert c.post("/api/admin/approve-all",
                  headers={"Cookie": f"beermap_admin={token}"}).status_code == 200


def test_admin_login_rejects_garbage_expired_and_tampered(client):
    from datetime import datetime, timedelta

    from api import admin_links
    c, _ = client
    assert c.get("/admin/login?key=garbage",
                 follow_redirects=False).status_code == 403
    old = datetime.now() - timedelta(seconds=config.ADMIN_LINK_TTL_S + 60)
    assert c.get(f"/admin/login?key={admin_links.make_token(old)}",
                 follow_redirects=False).status_code == 403
    expiry, _, sig = admin_links.make_token(datetime.now()).partition(".")
    forged = f"{int(expiry) + 9999}.{sig}"  # extend expiry without re-signing
    assert c.get(f"/admin/login?key={forged}",
                 follow_redirects=False).status_code == 403
    # A bad cookie falls back to the basic-auth challenge, not a 403.
    assert c.get("/admin",
                 headers={"Cookie": "beermap_admin=garbage"}).status_code == 401


def test_admin_links_disabled_without_password(monkeypatch):
    from datetime import datetime

    from api import admin_links
    monkeypatch.setattr(config, "ADMIN_PW", "")
    assert admin_links.make_token(datetime.now()) is None
    assert admin_links.verify_token("123.abc", datetime.now()) is False


def test_notification_carries_fresh_login_link(monkeypatch):
    from api import notify
    monkeypatch.setattr(config, "ADMIN_PW", "secret")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-100123456789")
    sent = {}
    monkeypatch.setattr(notify.httpx, "post",
                        lambda url, json, timeout: sent.update(json))
    notify.notify_new_submission("Astra", "node/1")
    assert "/admin/login?key=" in sent["text"]
    assert sent["disable_web_page_preview"] is True


def test_notification_email_channel(monkeypatch):
    from api import notify
    monkeypatch.setattr(config, "ADMIN_PW", "secret")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    for k, v in dict(SMTP_HOST="smtp.example.com", SMTP_USER="beer@example.com",
                     SMTP_PASS="tok", SMTP_TO="mod@example.com").items():
        monkeypatch.setattr(config, k, v)
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, pw):
            pass

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    notify.notify_new_submission("Astra", "node/1")
    assert sent["host"] == "smtp.example.com"
    assert "/admin/login?key=" in sent["msg"].get_content()
    # The token stays out of the subject: subject lines are not E2E-encrypted.
    assert "key=" not in sent["msg"]["Subject"]


def test_add_venue_ignores_a_client_supplied_venue_osm_id(client):
    c, _ = client
    c.post("/api/submit", json={"kind": "add_venue", "name": "Craft Eck", "venue_osm_id": "node/1",
                                "address": "Musterstraße 5, 20357 Hamburg"})
    assert list_submissions(get_connection(config.DB_PATH), "pending")[0]["venue_osm_id"] == ""


def test_client_ip_trusts_cf_header_only_from_cloudflare():
    from api.app import client_ip
    assert client_ip("172.69.1.1", "9.9.9.9") == "9.9.9.9"       # CF edge: visitor
    assert client_ip("2606:4700::1", "9.9.9.9") == "9.9.9.9"
    assert client_ip("203.0.113.5", "9.9.9.9") == "203.0.113.5"  # not CF: forged header ignored
    assert client_ip("172.69.1.1", "") == "172.69.1.1"
    assert client_ip("172.69.1.1", "junk") == "172.69.1.1"
    assert client_ip("unknown", "9.9.9.9") == "unknown"


def test_client_ip_ipv6_keys_on_64_and_maps_v4():
    from api.app import client_ip
    a = client_ip("172.69.1.1", "2001:db8:1:2:aaaa::1")
    assert a == client_ip("172.69.1.1", "2001:db8:1:2:bbbb::9") == "2001:db8:1:2::"
    assert a != client_ip("172.69.1.1", "2001:db8:1:3::1")
    assert client_ip("172.69.1.1", "::ffff:9.9.9.9") == "9.9.9.9"


def test_ip_key_dropped_after_an_hour(client):
    from datetime import datetime, timedelta
    from pipeline.db import expire_ip_keys
    c, _ = client
    c.post("/api/submit", json={"venue_osm_id": "node/1", "brand": "Astra",
                                "serving": "fass", "kind": "add"},
           headers={"X-Forwarded-For": "9.9.9.9"})
    conn = get_connection(config.DB_PATH)
    expire_ip_keys(conn)
    assert list_submissions(conn, "pending")[0]["submitter_ip"] != ""  # inside the hour
    expire_ip_keys(conn, datetime.now() + timedelta(hours=2))
    assert list_submissions(conn, "pending")[0]["submitter_ip"] == ""


def test_address_looked_up_once_and_cached(client, monkeypatch):
    import api.app as app_mod
    c, _ = client
    calls = []
    monkeypatch.setattr(app_mod, "lookup_address",
                        lambda osm_id: calls.append(osm_id) or "Neue Allee 7, 22083 Hamburg")
    for _ in range(2):
        r = c.get("/api/address/node/1")
        assert r.json() == {"address": "Neue Allee 7, 22083 Hamburg"}
    assert calls == ["node/1"]
    assert c.get("/api/address/node/999").status_code == 404


def test_address_failure_is_not_cached(client, monkeypatch):
    import api.app as app_mod
    c, _ = client
    monkeypatch.setattr(app_mod, "lookup_address", lambda osm_id: None)
    assert c.get("/api/address/node/1").json() == {"address": None}
    monkeypatch.setattr(app_mod, "lookup_address", lambda osm_id: "")
    assert c.get("/api/address/node/1").json() == {"address": None}
    monkeypatch.setattr(app_mod, "lookup_address", lambda osm_id: "should not be asked")
    assert c.get("/api/address/node/1").json() == {"address": None}


# --- Partner API (/api/partner/submit, docs/PARTNER-API.md) ---------------

_TOKEN = "test-secret-123"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_HINT = {"name": "Hospoda U Testu", "lat": 50.08, "lon": 14.42}   # Prague, synthetic


@pytest.fixture
def partner(client, monkeypatch):
    monkeypatch.setattr(config, "PARTNER_TOKENS", {"dopivatel": _TOKEN, "other": "other-secret"})
    return client


def _partner(c, headers=_AUTH, **kw):
    body = dict(ref="r1", venue_osm_id="node/1", brand="Kozel", serving="tank", kind="add")
    body.update(kw)
    return c.post("/api/partner/submit", json=body, headers=headers)


def test_partner_auth_missing_wrong_and_right_token(partner):
    c, _ = partner
    assert _partner(c, headers={}).status_code == 401
    assert _partner(c, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert _partner(c, headers={"Authorization": f"Basic {_TOKEN}"}).status_code == 401
    # A bad body without a token still gets 401, never a validation hint.
    assert _partner(c, headers={}, ref=None).status_code == 401
    assert _partner(c).json() == {"ok": True}
    row = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert row["partner"] == "dopivatel" and row["ref"] == "r1"
    assert row["submitter_ip"] is None   # no IP to hash, nothing personal stored


def test_partner_endpoint_off_without_tokens(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(config, "PARTNER_TOKENS", {})
    assert _partner(c).status_code == 401
    assert _partner(c, headers={"Authorization": "Bearer "}).status_code == 401


def test_partner_tokens_parsed_from_env(monkeypatch):
    import importlib
    monkeypatch.setenv("PARTNER_TOKENS", " dopivatel:s3cr:et , nosecret: ,:orphan,junk")
    try:
        assert importlib.reload(config).PARTNER_TOKENS == {"dopivatel": "s3cr:et"}
    finally:
        monkeypatch.delenv("PARTNER_TOKENS")
        importlib.reload(config)


def test_partner_ref_is_idempotent_per_partner(partner):
    c, _ = partner
    assert _partner(c).json() == {"ok": True}
    assert _partner(c, brand="Gambrinus").json() == {"ok": True, "duplicate": True}
    other = {"Authorization": "Bearer other-secret"}
    assert _partner(c, headers=other).json() == {"ok": True}   # same ref, other partner
    rows = list_submissions(get_connection(config.DB_PATH), "pending")
    assert [(r["partner"], r["brand"]) for r in rows] == [("dopivatel", "Kozel"), ("other", "Kozel")]


def test_partner_validation_is_400(partner):
    c, _ = partner
    bad = [dict(ref=""), dict(ref="x" * 65), dict(kind="edit_hours", opening_hours="Mo 10:00-12:00"),
           dict(serving="lager"), dict(brand=""), dict(ref=None), dict(venue={"lat": "x", "lon": 1})]
    for kw in bad:
        r = _partner(c, **kw)
        assert r.status_code == 400 and "detail" in r.json(), kw
    assert _partner(c, ref="u", serving="unknown").json() == {"ok": True}  # partners may not know


def test_partner_unknown_venue_needs_a_hint(partner):
    c, _ = partner
    r = _partner(c, venue_osm_id="node/777")
    assert r.status_code == 400 and "venue" in r.json()["detail"]
    assert _partner(c, venue_osm_id="manual/foo", venue=_HINT).status_code == 400  # not an OSM id
    assert _partner(c, venue_osm_id="node/777",
                    venue=dict(_HINT, lat=40.0)).status_code == 400              # off the map
    assert _partner(c, venue_osm_id="node/777", venue=dict(_HINT, name="")).status_code == 400
    assert list_submissions(get_connection(config.DB_PATH), "pending") == []
    assert _partner(c, venue_osm_id="node/777", venue=_HINT).json() == {"ok": True}
    row = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert (row["venue_name"], row["hint_lat"], row["hint_lon"]) == ("Hospoda U Testu", 50.08, 14.42)
    # Nothing exists until the moderator approves.
    assert get_connection(config.DB_PATH).execute(
        "SELECT 1 FROM venues WHERE osm_id='node/777'").fetchone() is None


def test_partner_hint_approval_creates_venue_and_survives_pipeline(partner, tmp_path):
    from pipeline.run import run_pipeline
    c, out = partner
    _partner(c, venue_osm_id="node/777", venue=_HINT, brand="kozel", serving="tank")
    page = c.get("/admin", auth=("admin", "secret")).text
    assert "Hospoda U Testu" in page and ">dopivatel<" in page and "Neu anlegen bei 50.08000" in page
    sid = list_submissions(get_connection(config.DB_PATH), "pending")[0]["id"]
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).json() == {"ok": True}

    def exported():
        fc = json.loads(open(out, encoding="utf-8").read())
        return {f["properties"]["name"]: f for f in fc["features"]}

    hosp = exported()["Hospoda U Testu"]
    assert hosp["geometry"]["coordinates"] == [14.42, 50.08]
    assert [(b["brand"], b["source"], b["serving"]) for b in hosp["properties"]["brands"]] == \
        [("Kozel", "community", "tank")]
    # The nightly build never sees node/777; the venue and its beer stay, and
    # a lost venue row is recreated from the hint on re-apply.
    run = dict(db_path=config.DB_PATH, out_path=out, curation_path=str(tmp_path / "none.yaml"),
               overpass_fetch=lambda: {"elements": []}, finders=[], today="2026-11-02")
    run_pipeline(**run)
    assert "Hospoda U Testu" in exported()
    conn = get_connection(config.DB_PATH)
    conn.execute("DELETE FROM venues WHERE osm_id='node/777'")
    conn.commit()
    run_pipeline(**run)
    assert exported()["Hospoda U Testu"]["properties"]["brands"][0]["brand"] == "Kozel"
    # The curation export carries the hint, so a fresh DB can recreate it too.
    from pipeline.curation import approved_community_entries
    entry = approved_community_entries(conn)[0]
    assert (entry["osm_id"], entry["venue"], entry["lat"], entry["lon"]) == \
        ("node/777", "Hospoda U Testu", 50.08, 14.42)


def test_partner_add_venue_uses_coordinates_not_geocoding(partner, monkeypatch):
    c, _ = partner
    assert _partner(c, kind="add_venue", venue_osm_id="", brand="", name="Nová Hospoda").status_code == 400
    assert _partner(c, kind="add_venue", venue_osm_id="", brand="", venue=_HINT).status_code == 400
    assert _partner(c, kind="add_venue", venue_osm_id="node/1", name="Nová Hospoda", brand="Kozel",
                    serving="unknown", venue={"lat": 49.2, "lon": 16.6}).json() == {"ok": True}
    row = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert (row["venue_osm_id"], row["venue_name"], row["lat"], row["lon"], row["address"]) == \
        ("", "Nová Hospoda", 49.2, 16.6, None)
    monkeypatch.setattr(submissions, "geocode_address",
                        lambda *a, **k: pytest.fail("partner add_venue must not geocode"))
    assert c.post(f"/api/admin/{row['id']}/approve", auth=("admin", "secret")).json() == {"ok": True}
    v = get_connection(config.DB_PATH).execute(
        "SELECT name, lat, lon FROM venues WHERE osm_id LIKE 'community/%'").fetchone()
    assert tuple(v) == ("Nová Hospoda", 49.2, 16.6)


def test_partner_edit_venue_renames(partner):
    c, _ = partner
    assert _partner(c, kind="edit_venue", brand="").status_code == 400   # neither name nor address
    assert _partner(c, kind="edit_venue", brand="", name="Bar Y").json() == {"ok": True}
    row = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert "Neuer Name: Bar Y" in c.get("/admin", auth=("admin", "secret")).text
    assert c.post(f"/api/admin/{row['id']}/approve", auth=("admin", "secret")).json() == {"ok": True}
    v = get_connection(config.DB_PATH).execute(
        "SELECT name, address, search_key FROM venues WHERE osm_id='node/1'").fetchone()
    assert (v["name"], v["address"], v["search_key"]) == ("Bar Y", None, "bar y")


def test_partner_daily_cap(partner, monkeypatch):
    c, _ = partner
    monkeypatch.setattr(config, "PARTNER_DAILY_CAP", 2)
    assert _partner(c, ref="a").status_code == 200
    assert _partner(c, ref="b").status_code == 200
    assert _partner(c, ref="c").status_code == 429
    assert _partner(c, ref="a").json() == {"ok": True, "duplicate": True}  # retries still answer
    assert _partner(c, ref="c", headers={"Authorization": "Bearer other-secret"}).status_code == 200
    assert _submit(c).status_code == 200    # the public endpoint has its own limit


@pytest.fixture
def telegram(monkeypatch):
    """Every message the notification channels deliver (Telegram stands in for both)."""
    from api import notify
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-100123456789")
    sent = []
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: sent.append(json["text"]))
    return sent


def _digest():
    from datetime import datetime
    from api import notify
    return notify.send_partner_digest(get_connection(config.DB_PATH), datetime.now())


def test_partner_submit_sends_no_immediate_notification(partner, telegram):
    c, _ = partner
    assert _partner(c).json() == {"ok": True}
    assert telegram == []
    assert _submit(c).json() == {"ok": True}   # the public endpoint still announces each one
    assert len(telegram) == 1 and "neue Einreichung" in telegram[0]


def test_partner_digest_sends_once_for_new_reports(partner, telegram):
    c, _ = partner
    assert _digest() == 0 and telegram == []          # no reports, no message
    for ref in ("a", "b"):
        _partner(c, ref=ref)
    _partner(c, ref="c", kind="close_venue", brand="")
    _partner(c, ref="d", headers={"Authorization": "Bearer other-secret"})
    _submit(c)                                        # public: announced on its own, not here
    telegram.clear()
    assert _digest() == 4
    assert len(telegram) == 1
    assert "dopivatel: 3 neue Meldungen (2× add, 1× close_venue)" in telegram[0]
    assert "other: 1 neue Meldung (1× add)" in telegram[0]
    assert "/admin" in telegram[0]
    assert _digest() == 0 and len(telegram) == 1      # a rerun the same night sends nothing
    _partner(c, ref="e")
    assert _digest() == 1 and "dopivatel: 1 neue Meldung " in telegram[1]


def test_nightly_run_sends_the_partner_digest(partner, telegram, monkeypatch):
    import pipeline.run as run_mod
    c, _ = partner
    _partner(c)
    monkeypatch.setattr(run_mod, "DB_PATH", config.DB_PATH)
    assert run_mod._partner_digest() == 1 and len(telegram) == 1


def test_partner_digest_keeps_reports_when_nothing_was_delivered(partner, monkeypatch):
    from api import notify
    c, _ = partner
    _partner(c)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")   # no channel configured
    assert _digest() == 0
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-100123456789")

    def down(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify.httpx, "post", down)
    assert _digest() == 0                                    # failed send: still owed
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: None)
    assert _digest() == 1                                    # the next night catches up


def test_public_submit_unchanged_by_partner_rules(partner):
    c, _ = partner
    assert _submit(c, serving="unknown").status_code == 400
    assert _submit(c, venue_osm_id="node/777", venue=_HINT).status_code == 400
    assert c.post("/api/submit", json={"venue_osm_id": "node/1", "kind": "edit_venue",
                                       "name": "Bar Y"}).status_code == 400
    assert c.post("/api/submit", json={"kind": "add_venue", "name": "X"}).status_code == 400
    assert c.post("/api/submit", json={"kind": "bogus"}).status_code == 400
    assert c.post("/api/submit", json={"venue_osm_id": 1}).status_code == 422   # FastAPI default
    assert _submit(c, ref="r1").json() == {"ok": True}
    row = list_submissions(get_connection(config.DB_PATH), "pending")[0]
    assert (row["partner"], row["ref"], row["submitter_ip"][:2]) == (None, None, "h:")
