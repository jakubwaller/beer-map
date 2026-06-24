# Live Curation (Web Submissions + Moderation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let anyone add/correct a venue's beers live from the website, with a maintainer moderation queue, served entirely from the Pi.

**Architecture:** A FastAPI app (uvicorn) on the Pi serves the existing static `web/` **and** an API: `POST /api/submit` (anonymous, rate-limited, honeypot) writes to a new `submissions` table; a Basic-auth `/admin` queue approves/rejects; approval applies a `source="community"` edge and re-exports `venues.json` instantly. The daily cron pipeline re-applies approved submissions alongside `curation.yaml`. Same origin → no CORS.

**Tech Stack:** FastAPI + uvicorn, existing SQLite (WAL), Resend for notifications, vanilla JS frontend. Tested with `fastapi.testclient.TestClient` + pytest.

## Global Constraints

- **Python 3.9+** — every new module starts with `from __future__ import annotations`.
- **All on the Pi, one origin** — FastAPI serves `web/` static + `/api` + `/admin`; no CORS, no third-party CDN.
- **Trust order: `manual` > `community` > `finder:*` > `osm`** — enforced in `fetch_venues_with_brands` SQL and `web/datasource.js`; community edits show a "✓ geprüft" badge.
- **Serving type:** `serving ∈ {fass, tank}` for `add` submissions.
- **Pending submissions are never exported.** Only `approved` rows become edges.
- **Store submitter IP only for rate-limiting** — no other personal data, no review text.
- **Secrets via env only** (`BEERMAP_ADMIN_PW`, `RESEND_API_KEY`, …) — never committed.
- **`curation.yaml` stays the maintainer's highest-trust hand-edit path;** community submissions live in the DB.
- **DNS is already done:** `beermap.jakubwaller.eu` (A 77.22.117.216 + AAAA, proxied:false) resolves to the Pi; deployment only needs a Caddy block + a uvicorn service.
- **Commits:** conventional-commit messages, no attribution trailers.

## File Structure

```
beer-map/
├── pipeline/
│   ├── db.py            # MODIFY: WAL pragma, submissions table + CRUD, community ordering
│   ├── submissions.py   # NEW: validate, rate-limit, apply, approve/reject
│   ├── run.py           # MODIFY: apply approved submissions after curation
│   └── config.py        # MODIFY: admin/resend/rate-limit settings
├── api/
│   ├── __init__.py      # NEW (empty)
│   ├── app.py           # NEW: FastAPI app (static + /api + /admin)
│   └── notify.py        # NEW: best-effort Resend notification
├── web/
│   ├── datasource.js    # MODIFY: community trust rank
│   ├── datasource.test.js # MODIFY: community ordering test
│   ├── app.js           # MODIFY: submit form in popup + community badge
│   └── style.css        # MODIFY: form + community badge styles
├── deploy/
│   ├── beermap.service  # NEW: systemd unit for uvicorn
│   ├── beermap.caddy    # NEW: Caddy site block
│   └── DEPLOY.md        # NEW: deployment steps
├── tests/
│   ├── test_db.py       # MODIFY: submissions CRUD + ordering
│   ├── test_submissions.py # NEW
│   └── test_api.py      # NEW
├── requirements.txt     # MODIFY: fastapi, uvicorn
└── README.md            # MODIFY: API + deploy notes
```

---

### Task 1: DB layer — submissions table, WAL, CRUD, community ordering

**Files:**
- Modify: `pipeline/db.py`
- Test: `tests/test_db.py` (add cases)

**Interfaces:**
- Consumes: existing `get_connection`, `init_db`, `Venue`.
- Produces:
  - `insert_submission(conn, sub:dict, created_at:str)->int` where `sub` has keys `kind, venue_osm_id, venue_name, lat, lon, brand, serving, note, submitter_ip`
  - `list_submissions(conn, status:str='pending')->list[dict]`
  - `get_submission(conn, sub_id:int)->dict|None`
  - `set_submission_status(conn, sub_id:int, status:str, decided_at:str)->None`
  - `count_submissions_since(conn, ip:str, since_iso:str)->int`
  - `fetch_venues_with_brands` now ranks `community` between `manual` and `finder:*`.

- [ ] **Step 1: Add the failing tests to `tests/test_db.py`**

```python
from pipeline.db import (
    insert_submission, list_submissions, get_submission,
    set_submission_status, count_submissions_since,
)


def _sub(**kw):
    base = dict(kind="add", venue_osm_id="node/1", venue_name="Bar X", lat=None, lon=None,
                brand="Astra", serving="fass", note=None, submitter_ip="1.2.3.4")
    base.update(kw)
    return base


def test_submission_crud_and_status():
    conn = _conn()
    sid = insert_submission(conn, _sub(), "2026-06-24T10:00:00")
    pending = list_submissions(conn, "pending")
    assert len(pending) == 1 and pending[0]["brand"] == "Astra" and pending[0]["status"] == "pending"
    set_submission_status(conn, sid, "approved", "2026-06-24T11:00:00")
    assert get_submission(conn, sid)["status"] == "approved"
    assert list_submissions(conn, "pending") == []


def test_count_submissions_since():
    conn = _conn()
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:00:00")
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:30:00")
    insert_submission(conn, _sub(submitter_ip="8.8.8.8"), "2026-06-24T10:30:00")
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T10:15:00") == 1
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T09:00:00") == 2


def test_community_ranks_between_manual_and_finder():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    for src in ("osm", "finder:Ratsherrn", "community", "manual"):
        bid = upsert_brand(conn, f"B-{src}")
        upsert_edge(conn, vid, bid, src, "2026-06-24")
    order = [b["source"] for b in fetch_venues_with_brands(conn)[0]["brands"]]
    assert order == ["manual", "community", "finder:Ratsherrn", "osm"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db.py -q`
Expected: FAIL — `ImportError: cannot import name 'insert_submission'`.

- [ ] **Step 3: Implement in `pipeline/db.py`**

Add to `SCHEMA` (inside the triple-quoted string, after the `venue_brand` table):
```sql
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    venue_osm_id TEXT,
    venue_name TEXT NOT NULL,
    lat REAL, lon REAL,
    brand TEXT NOT NULL,
    serving TEXT NOT NULL DEFAULT 'unknown',
    note TEXT,
    submitter_ip TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
```

In `get_connection`, after the `foreign_keys` pragma add:
```python
    conn.execute("PRAGMA journal_mode = WAL")
```

In `fetch_venues_with_brands`, replace the `ORDER BY ... CASE` block with:
```sql
            ORDER BY
                CASE vb.source
                     WHEN 'manual' THEN 0
                     WHEN 'community' THEN 1
                     ELSE CASE WHEN vb.source LIKE 'finder:%' THEN 2 ELSE 3 END END,
                b.name
```

Append these functions to `pipeline/db.py`:
```python
_SUB_COLS = ("kind", "venue_osm_id", "venue_name", "lat", "lon",
             "brand", "serving", "note", "submitter_ip")


def insert_submission(conn, sub: dict, created_at: str) -> int:
    cols = list(_SUB_COLS) + ["status", "created_at"]
    vals = [sub.get(c) for c in _SUB_COLS] + ["pending", created_at]
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO submissions ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def list_submissions(conn, status: str = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM submissions WHERE status=? ORDER BY created_at", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_submission(conn, sub_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
    return dict(row) if row else None


def set_submission_status(conn, sub_id: int, status: str, decided_at: str) -> None:
    conn.execute("UPDATE submissions SET status=?, decided_at=? WHERE id=?",
                 (status, decided_at, sub_id))
    conn.commit()


def count_submissions_since(conn, ip: str, since_iso: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE submitter_ip=? AND created_at >= ?",
        (ip, since_iso),
    ).fetchone()["n"]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_db.py -q`
Expected: PASS (all, including the 3 new tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat: submissions table, WAL, CRUD, and community trust ordering"
```

---

### Task 2: Submission logic + pipeline integration

**Files:**
- Create: `pipeline/submissions.py`
- Modify: `pipeline/run.py`, `pipeline/config.py`
- Test: `tests/test_submissions.py`, `tests/test_run.py` (update summary)

**Interfaces:**
- Consumes: `db.{upsert_brand, upsert_edge, delete_edges, get_submission, set_submission_status, list_submissions, count_submissions_since}`, `config.normalize_brand`, `export.export_geojson`.
- Produces:
  - `validate_submission(payload:dict)->str|None` (None = ok, else error message)
  - `within_rate_limit(conn, ip:str, now:datetime)->bool`
  - `apply_one(conn, sub:dict, today:str)->bool`
  - `apply_approved(conn, today:str)->int`
  - `approve_submission(conn, sub_id:int, today:str, out_path:str)->bool`
  - `reject_submission(conn, sub_id:int, today:str)->bool`
- `config` gains `RATE_LIMIT`, `RATE_WINDOW_S`, `ADMIN_USER`, `ADMIN_PW`, `WEB_DIR`, `RESEND_API_KEY`, `NOTIFY_TO`, `NOTIFY_FROM`.

- [ ] **Step 1: Add settings to `pipeline/config.py`**

Add at the top after the existing imports line (add `import os` as the first line under `from __future__ import annotations`):
```python
import os

ADMIN_USER = os.environ.get("BEERMAP_ADMIN_USER", "admin")
ADMIN_PW = os.environ.get("BEERMAP_ADMIN_PW", "")
RATE_LIMIT = int(os.environ.get("BEERMAP_RATE_LIMIT", "10"))
RATE_WINDOW_S = int(os.environ.get("BEERMAP_RATE_WINDOW_S", "3600"))
WEB_DIR = "web"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
NOTIFY_TO = os.environ.get("BEERMAP_NOTIFY_TO", "")
NOTIFY_FROM = os.environ.get("BEERMAP_NOTIFY_FROM", "")
```

- [ ] **Step 2: Write the failing test `tests/test_submissions.py`**

```python
from datetime import datetime

from pipeline.db import (
    get_connection, init_db, insert_submission, get_submission,
    upsert_venue, fetch_venues_with_brands,
)
from pipeline.models import Venue
from pipeline.submissions import (
    validate_submission, within_rate_limit, apply_approved,
    approve_submission, reject_submission,
)


def _seed():
    conn = get_connection(":memory:")
    init_db(conn)
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    return conn


def test_validate_submission():
    ok = dict(kind="add", venue_osm_id="node/1", brand="Astra", serving="fass")
    assert validate_submission(ok) is None
    assert validate_submission({**ok, "serving": "lager"})  # bad serving -> error
    assert validate_submission({**ok, "brand": ""})         # empty brand -> error
    assert validate_submission({**ok, "kind": "delete"})    # bad kind -> error


def test_rate_limit_trips_after_limit():
    conn = _seed()
    now = datetime(2026, 6, 24, 12, 0, 0)
    for _ in range(10):
        insert_submission(conn, dict(kind="add", venue_osm_id="node/1", venue_name="Bar X",
                                     lat=None, lon=None, brand="Astra", serving="fass",
                                     note=None, submitter_ip="1.1.1.1"), now.isoformat())
    assert within_rate_limit(conn, "1.1.1.1", now) is False
    assert within_rate_limit(conn, "2.2.2.2", now) is True


def test_approve_applies_community_edge_and_reject_does_not():
    conn = _seed()
    sid = insert_submission(conn, dict(kind="add", venue_osm_id="node/1", venue_name="Bar X",
                                       lat=None, lon=None, brand="pilsner urquell",
                                       serving="tank", note=None, submitter_ip="1.1.1.1"),
                            "2026-06-24T10:00:00")
    assert approve_submission(conn, sid, "2026-06-24", "/dev/null") is True
    brands = fetch_venues_with_brands(conn)[0]["brands"]
    assert brands == [{"brand": "Pilsner Urquell", "source": "community",
                       "serving": "tank", "last_seen": "2026-06-24"}]
    assert get_submission(conn, sid)["status"] == "approved"

    sid2 = insert_submission(conn, dict(kind="add", venue_osm_id="node/1", venue_name="Bar X",
                                        lat=None, lon=None, brand="Jever", serving="fass",
                                        note=None, submitter_ip="1.1.1.1"), "2026-06-24T10:05:00")
    assert reject_submission(conn, sid2, "2026-06-24") is True
    assert get_submission(conn, sid2)["status"] == "rejected"
    names = {b["brand"] for b in fetch_venues_with_brands(conn)[0]["brands"]}
    assert "Jever" not in names


def test_apply_approved_is_idempotent():
    conn = _seed()
    sid = insert_submission(conn, dict(kind="add", venue_osm_id="node/1", venue_name="Bar X",
                                       lat=None, lon=None, brand="Astra", serving="fass",
                                       note=None, submitter_ip="1.1.1.1"), "2026-06-24T10:00:00")
    from pipeline.db import set_submission_status
    set_submission_status(conn, sid, "approved", "2026-06-24")
    assert apply_approved(conn, "2026-06-24") == 1
    assert apply_approved(conn, "2026-06-24") == 1  # idempotent, still one edge
    assert len(fetch_venues_with_brands(conn)[0]["brands"]) == 1
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_submissions.py -q`
Expected: FAIL — `ModuleNotFoundError: pipeline.submissions`.

- [ ] **Step 4: Write `pipeline/submissions.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta

from . import config
from .db import (
    count_submissions_since, delete_edges, get_submission, list_submissions,
    set_submission_status, upsert_brand, upsert_edge,
)
from .export import export_geojson

_SERVINGS = {"fass", "tank"}


def validate_submission(payload: dict) -> str | None:
    if payload.get("kind") not in ("add", "remove"):
        return "kind must be 'add' or 'remove'"
    if not payload.get("venue_osm_id"):
        return "venue_osm_id required"
    brand = (payload.get("brand") or "").strip()
    if not brand or len(brand) > 80:
        return "brand must be 1-80 chars"
    if payload.get("kind") == "add" and payload.get("serving") not in _SERVINGS:
        return "serving must be 'fass' or 'tank'"
    if payload.get("note") and len(payload["note"]) > 300:
        return "note too long"
    return None


def within_rate_limit(conn, ip: str, now: datetime) -> bool:
    since = (now - timedelta(seconds=config.RATE_WINDOW_S)).isoformat()
    return count_submissions_since(conn, ip, since) < config.RATE_LIMIT


def _venue_id(conn, osm_id):
    row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    return row["id"] if row else None


def apply_one(conn, sub: dict, today: str) -> bool:
    vid = _venue_id(conn, sub.get("venue_osm_id"))
    if vid is None:
        return False
    bid = upsert_brand(conn, config.normalize_brand(sub["brand"]))
    if sub.get("kind") == "remove":
        delete_edges(conn, vid, bid)
    else:
        upsert_edge(conn, vid, bid, "community", today,
                    serving=sub.get("serving", "unknown"))
    return True


def apply_approved(conn, today: str) -> int:
    n = 0
    for sub in list_submissions(conn, "approved"):
        if apply_one(conn, sub, today):
            n += 1
    return n


def approve_submission(conn, sub_id: int, today: str, out_path: str) -> bool:
    sub = get_submission(conn, sub_id)
    if not sub or sub["status"] != "pending":
        return False
    apply_one(conn, sub, today)
    set_submission_status(conn, sub_id, "approved", today)
    conn.commit()
    export_geojson(conn, out_path)
    return True


def reject_submission(conn, sub_id: int, today: str) -> bool:
    sub = get_submission(conn, sub_id)
    if not sub or sub["status"] != "pending":
        return False
    set_submission_status(conn, sub_id, "rejected", today)
    conn.commit()
    return True
```

- [ ] **Step 5: Wire into `pipeline/run.py`**

Add `submissions` to the package import line:
```python
from . import curation, osm, submissions
```
After the `cur = curation.apply_curation(...)` line and before `conn.commit()`, add:
```python
    community = submissions.apply_approved(conn, today)
```
Add `"community": community,` to the returned summary dict (e.g. right after `"manual_removed": cur["removed"],`).

- [ ] **Step 6: Update `tests/test_run.py` summary expectation**

In `test_run_pipeline_applies_curation_over_finders`, change the expected `summary` dict to include the new key:
```python
    assert summary == {
        "venues": 2, "osm_edges": 1, "finder_edges": 1,
        "unmatched": 0, "manual_added": 1, "manual_removed": 1,
        "community": 0, "exported": 2,
    }
```

- [ ] **Step 7: Run to verify pass**

Run: `pytest tests/test_submissions.py tests/test_run.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/submissions.py pipeline/run.py pipeline/config.py tests/test_submissions.py tests/test_run.py
git commit -m "feat: submission validation, rate-limit, apply, and pipeline integration"
```

---

### Task 3: FastAPI app (static + submit + admin)

**Files:**
- Create: `api/__init__.py` (empty), `api/app.py`
- Modify: `requirements.txt`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `db.*`, `submissions.*`, `config.*`, `pipeline.osm`/`Venue` (seed in tests).
- Produces: `api.app.create_app()->FastAPI`; module-level `app = create_app()` for `uvicorn api.app:app`. Routes: `GET /api/brands`, `POST /api/submit`, `GET /admin`, `POST /api/admin/{sub_id}/approve|reject`.

- [ ] **Step 1: Add deps to `requirements.txt`**

```
fastapi==0.115.5
uvicorn==0.32.1
```

- [ ] **Step 2: Write the failing test `tests/test_api.py`**

```python
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
    conn = get_connection(config.DB_PATH)
    assert len(list_submissions(conn, "pending")) == 1


def test_honeypot_silently_dropped(client):
    c, _ = client
    assert _submit(c, hp="i-am-a-bot").json() == {"ok": True}
    conn = get_connection(config.DB_PATH)
    assert list_submissions(conn, "pending") == []


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
    sid = _submit(c, brand="pilsner urquell", serving="tank").json() and 1
    assert c.get("/admin").status_code == 401
    page = c.get("/admin", auth=("admin", "secret"))
    assert page.status_code == 200 and "Bar X" in page.text
    assert c.post(f"/api/admin/{sid}/approve", auth=("admin", "secret")).json() == {"ok": True}
    fc = json.loads(open(out, encoding="utf-8").read())
    brands = fc["features"][0]["properties"]["brands"]
    assert brands == [{"brand": "Pilsner Urquell", "source": "community",
                       "serving": "tank", "last_seen": pytest.approx(brands[0]["last_seen"])
                       if False else brands[0]["last_seen"]}]


def test_brands_endpoint(client):
    c, _ = client
    assert c.get("/api/brands").json() == []  # no edges yet
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: api.app`.

- [ ] **Step 4: Create `api/__init__.py` (empty) and write `api/app.py`**

```python
from __future__ import annotations

import html
import secrets
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import config, submissions
from pipeline.db import (
    get_connection, init_db, insert_submission, list_submissions,
)
from pipeline import notify

_basic = HTTPBasic()


class Submission(BaseModel):
    venue_osm_id: str
    brand: str
    serving: str = "unknown"
    kind: str = "add"
    note: str | None = None
    hp: str | None = None  # honeypot


def _db():
    conn = get_connection(config.DB_PATH)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _require_admin(creds: HTTPBasicCredentials = Depends(_basic)):
    ok = bool(config.ADMIN_PW) and \
        secrets.compare_digest(creds.username, config.ADMIN_USER) and \
        secrets.compare_digest(creds.password, config.ADMIN_PW)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/brands")
    def brands(conn=Depends(_db)):
        rows = conn.execute("SELECT name FROM brands ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    @app.post("/api/submit")
    def submit(sub: Submission, request: Request, conn=Depends(_db)):
        if sub.hp:  # bot filled the honeypot
            return {"ok": True}
        payload = sub.model_dump()
        err = submissions.validate_submission(payload)
        if err:
            raise HTTPException(400, err)
        if conn.execute("SELECT 1 FROM venues WHERE osm_id=?",
                        (sub.venue_osm_id,)).fetchone() is None:
            raise HTTPException(400, "unknown venue")
        ip = request.client.host if request.client else "unknown"
        if not submissions.within_rate_limit(conn, ip, datetime.now()):
            raise HTTPException(429, "rate limit exceeded")
        payload["venue_name"] = sub.venue_osm_id
        payload["submitter_ip"] = ip
        insert_submission(conn, payload, datetime.now().isoformat())
        notify.notify_new_submission(sub.brand, sub.venue_osm_id)
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin(conn=Depends(_db), _=Depends(_require_admin)):
        rows = list_submissions(conn, "pending")
        items = "".join(
            f"<li>#{r['id']} <b>{html.escape(r['kind'])}</b> "
            f"{html.escape(r['brand'])} ({html.escape(r['serving'])}) @ "
            f"{html.escape(r['venue_osm_id'] or '')} "
            f"<i>{html.escape(r['note'] or '')}</i> "
            f"<button onclick=\"d({r['id']},'approve')\">approve</button> "
            f"<button onclick=\"d({r['id']},'reject')\">reject</button></li>"
            for r in rows
        ) or "<li>nothing pending</li>"
        return (
            "<!doctype html><meta charset=utf-8><title>Moderation</title>"
            "<h1>Pending submissions</h1><ul>" + items + "</ul>"
            "<script>async function d(id,a){await fetch('/api/admin/'+id+'/'+a,"
            "{method:'POST'});location.reload()}</script>"
        )

    @app.post("/api/admin/{sub_id}/approve")
    def approve(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.approve_submission(conn, sub_id, date.today().isoformat(), config.OUT_PATH):
            raise HTTPException(404, "not pending")
        return {"ok": True}

    @app.post("/api/admin/{sub_id}/reject")
    def reject(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.reject_submission(conn, sub_id, date.today().isoformat()):
            raise HTTPException(404, "not pending")
        return {"ok": True}

    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="static")
    return app


app = create_app()
```

(Note: the admin `fetch` for approve/reject sends no body, so `python-multipart` is not needed.)

- [ ] **Step 5: Create `api/notify.py` stub so the import resolves** (full impl in Task 4)

```python
from __future__ import annotations


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    """Best-effort maintainer notification. Implemented in Task 4."""
    return None
```

- [ ] **Step 6: Simplify the brittle assertion in the test**

Replace the `brands == [...]` block inside `test_admin_requires_auth_and_approve_exports` with:
```python
    fc = json.loads(open(out, encoding="utf-8").read())
    brands = fc["features"][0]["properties"]["brands"]
    assert len(brands) == 1
    assert brands[0]["brand"] == "Pilsner Urquell"
    assert brands[0]["source"] == "community"
    assert brands[0]["serving"] == "tank"
```

- [ ] **Step 7: Install deps and run**

Run:
```bash
./.venv/bin/python -m pip install -q -r requirements.txt
./.venv/bin/python -m pytest tests/test_api.py -q
```
Expected: PASS (6 passed).

- [ ] **Step 8: Commit**

```bash
git add api/__init__.py api/app.py api/notify.py requirements.txt tests/test_api.py
git commit -m "feat: FastAPI app — submit, admin moderation, static serving"
```

---

### Task 4: Resend notification (best-effort)

**Files:**
- Modify: `api/notify.py`
- Test: `tests/test_api.py` (one monkeypatched case)

**Interfaces:**
- Produces: `notify.notify_new_submission(brand:str, venue_osm_id:str)->None` — posts to Resend if `config.RESEND_API_KEY` and `config.NOTIFY_TO` are set; never raises.

- [ ] **Step 1: Add the failing test to `tests/test_api.py`**

```python
def test_notify_is_best_effort(monkeypatch):
    from pipeline import config
    from api import notify
    monkeypatch.setattr(config, "RESEND_API_KEY", "")  # unconfigured -> no-op, no error
    assert notify.notify_new_submission("Astra", "node/1") is None

    calls = {}
    monkeypatch.setattr(config, "RESEND_API_KEY", "x")
    monkeypatch.setattr(config, "NOTIFY_TO", "me@example.com")
    monkeypatch.setattr(config, "NOTIFY_FROM", "bot@example.com")
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(notify.httpx, "post", boom)
    # must swallow the error
    assert notify.notify_new_submission("Astra", "node/1") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api.py::test_notify_is_best_effort -q`
Expected: FAIL — `notify.httpx` does not exist yet.

- [ ] **Step 3: Implement `api/notify.py`**

```python
from __future__ import annotations

import httpx

from pipeline import config


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    if not (config.RESEND_API_KEY and config.NOTIFY_TO and config.NOTIFY_FROM):
        return None
    try:
        httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={
                "from": config.NOTIFY_FROM,
                "to": [config.NOTIFY_TO],
                "subject": "beermap: neue Einreichung",
                "text": f"Neue Einreichung: {brand} @ {venue_osm_id}\nPrüfen: /admin",
            },
            timeout=10,
        )
    except Exception:
        pass  # notifications are best-effort
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/notify.py tests/test_api.py
git commit -m "feat: best-effort Resend notification on new submission"
```

---

### Task 5: Frontend — submit form, community badge, trust rank

**Files:**
- Modify: `web/datasource.js`, `web/datasource.test.js`, `web/app.js`, `web/style.css`
- Test: `web/datasource.test.js` (`node --test web/*.test.js`)

**Interfaces:**
- Consumes: `/api/submit`, `/api/brands`. `web/datasource.js` `rank()` now: manual<community<finder<osm.

- [ ] **Step 1: Update `web/datasource.test.js`** — add a community case

Replace the first feature's `brands` array in `FC` with one that includes a community edge and add a test:
```javascript
// in the first feature's properties.brands, add:
//   { brand: "Jever", source: "community", serving: "fass", last_seen: "2026-06-24" }
test("community ranks below manual and above osm/finder", () => {
  const v = loadVenues(FC);
  const sources = v[0].brands.map((b) => b.source);
  assert.equal(sources.indexOf("manual") < sources.indexOf("community"), true);
  assert.equal(sources.indexOf("community") < sources.indexOf("osm"), true);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test web/datasource.test.js`
Expected: FAIL (community currently ranks as "else" = same bucket as osm).

- [ ] **Step 3: Update `rank()` in `web/datasource.js`**

```javascript
function rank(source) {
  if (source === "manual") return 0;
  if (source === "community") return 1;
  if (source.startsWith("finder:")) return 2;
  return 3;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test web/datasource.test.js`
Expected: PASS.

- [ ] **Step 5: Add the community badge label in `web/app.js`**

In the `SOURCE_LABEL` object, add the community entry:
```javascript
const SOURCE_LABEL = { manual: "✓ verifiziert", community: "✓ geprüft", osm: "OSM" };
```
In `toFC`, give community edges the green badge too — change the `cls` line:
```javascript
      const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
```

- [ ] **Step 6: Add the submit form to the popup in `web/app.js`**

In `toFC`, append a submit form to the popup HTML. Replace the `properties: { html: ... }` so it ends with a form block:
```javascript
    properties: { osm_id: v.osm_id || "", html:
      `<strong>${esc(v.name)}</strong><br>${esc(v.address || "")}<br>` +
      v.brands.map((b) => {
        const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen].filter(Boolean).map(esc);
        const cls = (b.source === "manual" || b.source === "community") ? "badge manual" : "badge";
        return `${esc(b.brand)}<span class="${cls}">${parts.join(" · ")}</span>`;
      }).join("<br>") +
      `<form class="addbeer" data-osm="${esc(v.osm_id || "")}">
         <input name="brand" list="brandlist" placeholder="Marke" required>
         <label><input type="radio" name="serving" value="fass" checked>Fass</label>
         <label><input type="radio" name="serving" value="tank">Tank</label>
         <input class="hp" name="hp" tabindex="-1" autocomplete="off">
         <button>+ Bier melden</button><span class="msg"></span>
       </form>` } })) };
```
This needs `v.osm_id`, so in `loadVenues` (datasource.js) add `osm_id: f.properties.osm_id ?? f.id ?? ""` to the returned object — and ensure the GeoJSON carries it. In `pipeline/export.py`, add `"osm_id": r["osm_id"],` to each feature's `properties`. Add a Python assertion to `tests/test_export.py` if desired; the existing test still passes.

Wire form submit once, after the map loads (inside `map.on("load", ...)`, after layers are added):
```javascript
  document.getElementById("map").addEventListener("submit", async (ev) => {
    if (!ev.target.classList.contains("addbeer")) return;
    ev.preventDefault();
    const f = ev.target, msg = f.querySelector(".msg");
    const body = { venue_osm_id: f.dataset.osm, brand: f.brand.value.trim(),
                   serving: f.serving.value, kind: "add", hp: f.hp.value };
    const r = await fetch("/api/submit", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    msg.textContent = r.ok ? " Danke, wird geprüft!" : " Fehler";
    if (r.ok) f.querySelector("button").disabled = true;
  });
```
Add a `<datalist id="brandlist">` populated from `/api/brands` — after building the brand `<select>`, also:
```javascript
  const dl = document.createElement("datalist"); dl.id = "brandlist";
  for (const b of buildBrandList(allVenues)) {
    const o = document.createElement("option"); o.value = b; dl.appendChild(o);
  }
  document.body.appendChild(dl);
```

- [ ] **Step 7: Add form styles to `web/style.css`**

```css
.addbeer { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.addbeer input[name="brand"] { flex: 1 1 100%; padding: 4px; }
.addbeer .hp { position: absolute; left: -9999px; }
.addbeer .msg { font-size: 11px; color: #1c6b3c; }
```

- [ ] **Step 8: Verify frontend tests + manual smoke**

Run: `node --test web/*.test.js`
Expected: PASS. Manual: with the API running (Task 6), click a venue → form submits → "Danke, wird geprüft!"; the entry appears at `/admin`.

- [ ] **Step 9: Commit**

```bash
git add web/datasource.js web/datasource.test.js web/app.js web/style.css pipeline/export.py
git commit -m "feat: in-popup submit form, community badge, osm_id in export"
```

---

### Task 6: Deployment — uvicorn service + Caddy + docs

**Files:**
- Create: `deploy/beermap.service`, `deploy/beermap.caddy`, `deploy/DEPLOY.md`
- Modify: `README.md`

**Interfaces:** none (ops artifacts).

- [ ] **Step 1: Write `deploy/beermap.service`** (systemd unit; runs uvicorn on localhost:8011)

```ini
[Unit]
Description=beermap FastAPI
After=network-online.target

[Service]
WorkingDirectory=/home/ubuntu/beer-map
EnvironmentFile=/home/ubuntu/beer-map/deploy/beermap.env
ExecStart=/home/ubuntu/beer-map/.venv/bin/uvicorn api.app:app --host 127.0.0.1 --port 8011
Restart=always
RestartSec=3
User=ubuntu

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `deploy/beermap.caddy`** (Caddy site block; auto-TLS via Let's Encrypt)

```caddyfile
beermap.jakubwaller.eu {
	encode gzip
	reverse_proxy 127.0.0.1:8011
}
```

- [ ] **Step 3: Write `deploy/DEPLOY.md`**

````markdown
# Deploying beermap to the Pi

DNS is already set: `beermap.jakubwaller.eu` (A 77.22.117.216 + AAAA, proxied:false),
kept current by `~/pivalert/ip-address/check-ip-address.sh`.

## One-time
```bash
# on the Pi (ssh rpi)
cd ~ && git clone <repo-url> beer-map && cd beer-map
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cat > deploy/beermap.env <<EOF
BEERMAP_ADMIN_USER=admin
BEERMAP_ADMIN_PW=<choose-a-strong-password>
RESEND_API_KEY=<resend-key>
BEERMAP_NOTIFY_TO=jakub.waller@protonmail.com
BEERMAP_NOTIFY_FROM=beermap@termine.jakubwaller.eu
EOF
chmod 600 deploy/beermap.env
.venv/bin/python -m pipeline.run          # build the first venues.json

sudo cp deploy/beermap.service /etc/systemd/system/
sudo systemctl enable --now beermap
# add the Caddy block (append deploy/beermap.caddy to the Caddyfile or import it)
sudo systemctl reload caddy
```

## Updating data
- Daily cron rebuild (add to crontab):
  `0 4 * * * cd /home/ubuntu/beer-map && .venv/bin/python -m pipeline.run >> pipeline.log 2>&1`
- Approvals via `https://beermap.jakubwaller.eu/admin` apply + re-export instantly.

## Verify
```bash
curl -s https://beermap.jakubwaller.eu/api/brands | head
curl -s -o /dev/null -w "%{http_code}\n" https://beermap.jakubwaller.eu/
```
````

- [ ] **Step 4: Update `README.md`** — add an "API & moderation" section

Append:
```markdown
## API & live curation (on the Pi)
- `uvicorn api.app:app` serves the site + API on one origin.
- `POST /api/submit` — anonymous add/correct (rate-limited, honeypot).
- `/admin` — HTTP Basic (`BEERMAP_ADMIN_PW`) moderation queue; approve = instant re-export.
- Approved edits are `source="community"` (ranked below your `manual` curation.yaml).
- Deploy: see `deploy/DEPLOY.md` (DNS for beermap.jakubwaller.eu is already live).
```

- [ ] **Step 5: Full suite + commit**

Run:
```bash
./.venv/bin/python -m pytest -q
node --test web/*.test.js
```
Expected: all pass.
```bash
git add deploy/ README.md
git commit -m "docs: deployment (systemd + Caddy) and live-curation README"
```

---

## Self-Review Notes (completed by plan author)

- **Spec coverage:** submissions table + trust tier (Task 1) ✓; validation/rate-limit/apply/moderation (Task 2) ✓; pipeline re-applies approved (Task 2) ✓; FastAPI submit + admin + static, anti-abuse honeypot+rate-limit, Basic auth (Task 3) ✓; Resend best-effort (Task 4) ✓; popup form + community badge + datasource rank (Task 5) ✓; deployment on the Pi (Caddy + systemd, DNS already done) (Task 6) ✓. Deferred per spec: add-new-venue-by-pin, one-click email approval.
- **Type consistency:** submission dict keys (`kind, venue_osm_id, venue_name, lat, lon, brand, serving, note, submitter_ip, status`) are stable from `db.insert_submission` (Task 1) through `submissions.apply_one` (Task 2) and `api.app.submit` (Task 3). `source="community"` ranking is applied in SQL (Task 1) and `datasource.js` (Task 5). `notify_new_submission(brand, venue_osm_id)` signature matches between the Task 3 stub and the Task 4 impl.
- **Known judgment calls:** `venue_name` is stored as the `venue_osm_id` for MVP (form submits against an existing venue; the human-readable name is already on the map). Rate limit keys on `request.client.host` — behind Caddy this is the proxy IP unless Caddy forwards the real IP; DEPLOY note: Caddy sets `X-Forwarded-For`, so a follow-up may read it (the existing termine IP-limiter handles this pattern). Admin approve/reject use `date.today()` (server local date) for `last_seen`.
```
