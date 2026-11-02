import json

import pytest
from fastapi.testclient import TestClient

from pipeline import config, dedupe, submissions
from pipeline.curation import approved_community_entries
from pipeline.db import get_connection, get_submission, init_db, list_submissions, upsert_venue
from pipeline.models import Venue

AUTH = ("admin", "secret")
_TOKEN = "partner-secret"
# Troja, Prague: the add_venue pin, and OSM's fast_food node 6 m away.
LAT, LON = 50.11538, 14.41604
OSM_MAP = {"elements": [
    {"type": "node", "id": 3448110894, "lat": 50.11543, "lon": 14.41601,
     "tags": {"amenity": "fast_food", "name": "Trojbistro"}},
    {"type": "node", "id": 2137054117, "lat": 50.11540, "lon": 14.41606,
     "tags": {"amenity": "bench"}},  # 2 m away, but no venue
    {"type": "node", "id": 1, "lat": 50.11500, "lon": 14.41600},
    {"type": "node", "id": 2, "lat": 50.11500, "lon": 14.41650},
    {"type": "way", "id": 9, "nodes": [1, 2], "tags": {"name": "Povltavská"}},
]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "OUT_PATH", str(tmp_path / "venues.json"))
    monkeypatch.setattr(config, "ADMIN_PW", "secret")
    monkeypatch.setattr(config, "PARTNER_TOKENS", {"dopivatel": _TOKEN})
    conn = get_connection(str(db))
    init_db(conn)
    # 40 m north of the pin, same name modulo diacritics/case: a DB duplicate.
    upsert_venue(conn, Venue("node/77", "Hospoda Na Rohu", LAT + 0.00036, LON), "2026-11-01")
    upsert_venue(conn, Venue("node/78", "Ganz Woanders", LAT + 0.00036, LON), "2026-11-01")
    conn.commit()
    conn.close()
    from api.app import create_app
    return TestClient(create_app(), headers={"Sec-Fetch-Site": "same-origin"})


def _partner_add_venue(c, name, ref="r1"):
    r = c.post("/api/partner/submit", headers={"Authorization": f"Bearer {_TOKEN}"},
               json={"ref": ref, "kind": "add_venue", "name": name, "brand": "Kozel",
                     "serving": "tank", "venue": {"lat": LAT, "lon": LON}})
    assert r.json() == {"ok": True}
    return list_submissions(get_connection(config.DB_PATH), "pending")[-1]["id"]


def test_similar_names():
    assert dedupe.similar("Hospoda na rohu", "HOSPODA NA ROHU")
    assert dedupe.similar("Dvůr Kobylisy", "Dvůr")
    assert not dedupe.similar("Bar X", "X")          # too short to count as contained
    assert not dedupe.similar("Hallo", "Hall")        # whole words only


def test_db_duplicate_flagged_and_link_attaches_brand(client):
    sid = _partner_add_venue(client, "Hospoda na Rohu")
    page = client.get("/admin", auth=AUTH).text
    assert "Duplikat?" in page and "node/77" in page and "node/78" not in page
    assert "OSM nicht erreichbar" in page          # OSM down: still moderatable
    # approve-all leaves the suspected duplicate for a human
    assert client.post("/api/admin/approve-all", auth=AUTH).json()["approved"] == 0
    assert client.post(f"/api/admin/{sid}/link", auth=AUTH,
                       json={"osm_id": "node/78"}).status_code == 404  # not a candidate
    assert client.post(f"/api/admin/{sid}/link", auth=AUTH,
                       json={"osm_id": "node/77"}).json() == {"ok": True}
    assert client.post(f"/api/admin/{sid}/approve", auth=AUTH).json() == {"ok": True}
    conn = get_connection(config.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM venues WHERE osm_id LIKE 'community/%'").fetchone()[0] == 0
    row = conn.execute("SELECT v.name, b.name AS brand FROM venue_brand vb JOIN venues v ON v.id=vb.venue_id "
                       "JOIN brands b ON b.id=vb.brand_id").fetchone()
    assert row["name"] == "Hospoda Na Rohu" and row["brand"] == "Kozel"  # venue untouched
    [entry] = approved_community_entries(conn)
    assert entry["osm_id"] == "node/77" and entry["brand"] == "Kozel"


def test_osm_candidate_detected_cached_and_linked(client, monkeypatch):
    calls = []
    monkeypatch.setattr(dedupe, "_get_map", lambda *bbox: calls.append(bbox) or OSM_MAP)
    sid = _partner_add_venue(client, "Trojbistro")
    page = client.get("/admin", auth=AUTH).text
    assert "node/3448110894" in page and "fast_food" in page
    assert "node/2137054117" not in page and "Povltavská" not in page
    client.get("/admin", auth=AUTH)
    assert len(calls) == 1                         # cached on the row
    assert client.post(f"/api/admin/{sid}/link", auth=AUTH,
                       json={"osm_id": "node/3448110894"}).json() == {"ok": True}
    assert client.post(f"/api/admin/{sid}/approve", auth=AUTH).json() == {"ok": True}
    conn = get_connection(config.DB_PATH)
    v = conn.execute("SELECT name, lat, lon FROM venues WHERE osm_id='node/3448110894'").fetchone()
    assert v["name"] == "Trojbistro" and (v["lat"], v["lon"]) == (50.11543, 14.41601)
    [entry] = approved_community_entries(conn)
    assert entry["osm_id"] == "node/3448110894" and entry["lat"] == 50.11543


def test_unnamed_osm_venue_right_next_to_the_pin_counts(monkeypatch):
    monkeypatch.setattr(dedupe, "_get_map", lambda *bbox: {"elements": [
        {"type": "node", "id": 1494046262, "lat": LAT + 0.00002, "lon": LON,
         "tags": {"amenity": "fast_food"}}]})
    [c] = dedupe.osm_candidates("Občerstvení U Přívozu", LAT, LON)
    assert c["osm_id"] == "node/1494046262" and c["dist"] == 2


def test_osm_down_still_approvable_as_new(client):
    sid = _partner_add_venue(client, "KRO Libeň")
    page = client.get("/admin", auth=AUTH).text
    assert "Keine Duplikate gefunden" in page and "OSM nicht erreichbar" in page
    assert get_submission(get_connection(config.DB_PATH), sid)["osm_candidates"] is None
    assert client.post(f"/api/admin/{sid}/approve", auth=AUTH).json() == {"ok": True}
    conn = get_connection(config.DB_PATH)
    assert conn.execute("SELECT 1 FROM venues WHERE osm_id LIKE 'community/kro-%' AND name='KRO Libeň'").fetchone()


def test_unlink_restores_new_venue(client):
    sid = _partner_add_venue(client, "Hospoda na Rohu")
    client.post(f"/api/admin/{sid}/link", auth=AUTH, json={"osm_id": "node/77"})
    assert client.post(f"/api/admin/{sid}/link", auth=AUTH, json={"osm_id": ""}).json() == {"ok": True}
    assert get_submission(get_connection(config.DB_PATH), sid)["assigned_osm_id"] is None
