# Beer-on-Tap Map (Hamburg MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a static web map of Hamburg drinking venues, filterable by the draft beer brand and serving type (Fassbier/Tankbier) each one pours, with per-link provenance and freshness — built on a **human-curated core** seeded by automated sources.

**Architecture:** A Python pipeline (cron on a Raspberry Pi) builds the dataset in trust order: pull venues + `brewery=` tags from OpenStreetMap (low trust), enrich with brand "where to drink" finders (low trust, dated seeds), then apply a hand-edited `curation.yaml` (**highest trust** — can add *or remove* links, fixing staleness like "WALD switched from Pilsner Urquell to Budvar"). Everything lands in SQLite; an export step renders one `venues.json` (GeoJSON). A static MapLibre frontend reads it through a small `DataSource` module — the seam that later swaps to a bbox/brand API for all-Germany.

**Tech Stack:** Python 3 (httpx, beautifulsoup4, lxml, rapidfuzz, PyYAML, sqlite3 stdlib), pytest. Frontend: vanilla HTML/JS, self-hosted MapLibre GL JS, OSM raster tiles, `node --test` for the pure-function unit tests.

## Global Constraints

- **Python 3.10+** (uses `X | None` type syntax).
- **Runs unattended on a Raspberry Pi via cron** — no interactive prompts; pipeline is a single entrypoint `python -m pipeline.run`.
- **Curation is first-class.** A hand-edited `curation.yaml` is the highest-trust source (`source = "manual"`); the pipeline applies it **last** and it can both add and remove venue↔brand links. It is committed to git (it is the valuable data) — not gitignored.
- **Trust order: `manual` > `finder:*` > `osm`.** The frontend shows manual links as verified-with-date and orders them first.
- **Serving type tracked.** Every venue↔brand link records `serving ∈ {fass, tank, unknown}` so Fassbier and Tankbier are distinguishable and filterable.
- **Brands are open-ended.** Any brand string flows through; `BRAND_ALIASES` only normalizes known spellings.
- **Store no personal data and no review text** anywhere in the DB or export.
- **Scrapers:** every outbound HTTP request sets a descriptive `User-Agent`; finders are throttled and only fetch documented public pages.
- **Map attribution:** the frontend must show `© OpenStreetMap contributors` linked to `https://www.openstreetmap.org/copyright`, visible on the map.
- **No third-party CDNs.** MapLibre JS/CSS is self-hosted under `web/vendor/`; the frontend loads no external `<script>`/`<link>` (satisfies the Subresource-Integrity concern and avoids leaking visitor IPs to a CDN under GDPR).
- **Legal pages required:** `Impressum` (§5 DDG) and `Datenschutz` (GDPR).
- **Keep pipeline enrichment separate from raw OSM** — enrichment lives in our SQLite/`venues.json`, never written back into OSM.
- **No AI phone calls; no LLM in this MVP** (venue-menu LLM extraction is the agreed post-launch phase).
- **Commits:** conventional-commit messages, no co-author/attribution trailers.

## File Structure

```
beer-map/
├── pipeline/
│   ├── __init__.py
│   ├── config.py          # constants + brand normalization
│   ├── models.py          # Venue, BrandEdge, FinderEntry dataclasses
│   ├── db.py              # SQLite schema + upserts + delete + queries
│   ├── osm.py             # Overpass fetch + parse (venues + brewery edges)
│   ├── matching.py        # haversine + fuzzy name matching
│   ├── curation.py        # load + apply curation.yaml (manual layer)
│   ├── export.py          # SQLite -> venues.json (GeoJSON)
│   ├── run.py             # pipeline orchestration (cron entrypoint)
│   └── finders/
│       ├── __init__.py    # FINDERS registry
│       ├── base.py        # http_get + BaseFinder
│       ├── ratsherrn.py   # static-HTML finder (Fassbier)
│       └── pilsner_urquell.py  # Tankovna locator (Tankbier)
├── tests/
│   ├── test_db.py
│   ├── test_osm.py
│   ├── test_matching.py
│   ├── test_finders.py
│   ├── test_curation.py
│   ├── test_export.py
│   └── test_run.py
├── web/
│   ├── index.html
│   ├── style.css
│   ├── datasource.js      # pure query functions over the GeoJSON
│   ├── datasource.test.js # node --test
│   ├── app.js             # MapLibre wiring (manual test)
│   ├── vendor/            # self-hosted maplibre-gl.js + .css
│   ├── impressum.html
│   ├── datenschutz.html
│   └── data/.gitkeep      # venues.json is generated here
├── curation.yaml          # hand-curated venue<->brand truth (committed)
├── requirements.txt
├── pyproject.toml
├── .gitignore
└── README.md
```

---

### Task 1: Project skeleton + config + models + SQLite data layer

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `.gitignore`
- Create: `pipeline/__init__.py` (empty), `pipeline/config.py`, `pipeline/models.py`, `pipeline/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `pipeline.models.Venue(osm_id:str, name:str, lat:float, lon:float, address:str|None=None, website:str|None=None)`
  - `pipeline.models.BrandEdge(venue_osm_id:str, brand:str, source:str, serving:str="unknown", confidence:float=1.0)`
  - `pipeline.models.FinderEntry(name:str, brand:str, address:str|None=None, lat:float|None=None, lon:float|None=None, website:str|None=None, serving:str="unknown")`
  - `pipeline.config.{USER_AGENT, OVERPASS_URL, HAMBURG_QL, DB_PATH, OUT_PATH, CURATION_PATH, BRAND_ALIASES}`, `normalize_brand(name:str)->str`
  - `pipeline.db.get_connection(path)->sqlite3.Connection`, `init_db(conn)`, `upsert_venue(conn, venue, seen)->int`, `upsert_brand(conn, name)->int`, `upsert_edge(conn, venue_id, brand_id, source, seen, serving="unknown", confidence=1.0)->None`, `delete_edges(conn, venue_id, brand_id)->int`, `fetch_venues_with_brands(conn)->list[dict]`

- [ ] **Step 1: Create dependency + tooling files**

`requirements.txt`:
```
httpx==0.27.2
beautifulsoup4==4.12.3
lxml==5.3.0
rapidfuzz==3.10.1
PyYAML==6.0.2
pytest==8.3.3
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
*.sqlite
web/data/venues.json
```

- [ ] **Step 2: Write `pipeline/config.py`**

```python
USER_AGENT = "beer-map/0.1 (+https://github.com/; contact: set-me@example.com)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HAMBURG_QL = (
    '[out:json][timeout:90];'
    'area["name"="Hamburg"]["admin_level"="4"]->.a;'
    'nwr["amenity"~"^(pub|bar|biergarten|restaurant|cafe)$"](area.a);'
    'out center tags;'
)
DB_PATH = "beer-map.sqlite"
OUT_PATH = "web/data/venues.json"
CURATION_PATH = "curation.yaml"

# Normalize known brand spellings to a canonical display name.
BRAND_ALIASES = {
    "ratsherrn pils": "Ratsherrn",
    "ratsherrn": "Ratsherrn",
    "astra urtyp": "Astra",
    "astra": "Astra",
    "pilsner urquell": "Pilsner Urquell",
    "plzeňský prazdroj": "Pilsner Urquell",
    "plzensky prazdroj": "Pilsner Urquell",
    "urquell": "Pilsner Urquell",
    "budweiser budvar": "Budweiser Budvar",
    "budvar": "Budweiser Budvar",
}


def normalize_brand(name: str) -> str:
    return BRAND_ALIASES.get(name.strip().lower(), name.strip())
```

- [ ] **Step 3: Write `pipeline/models.py`**

```python
from dataclasses import dataclass


@dataclass
class Venue:
    osm_id: str
    name: str
    lat: float
    lon: float
    address: str | None = None
    website: str | None = None


@dataclass
class BrandEdge:
    venue_osm_id: str
    brand: str
    source: str
    serving: str = "unknown"
    confidence: float = 1.0


@dataclass
class FinderEntry:
    name: str
    brand: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    website: str | None = None
    serving: str = "unknown"
```

- [ ] **Step 4: Write the failing test `tests/test_db.py`**

```python
from pipeline.db import (
    get_connection, init_db, upsert_venue, upsert_brand,
    upsert_edge, delete_edges, fetch_venues_with_brands,
)
from pipeline.models import Venue


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_upsert_venue_is_idempotent_by_osm_id():
    conn = _conn()
    id1 = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    id2 = upsert_venue(conn, Venue("node/1", "Bar X (renamed)", 53.5, 10.0), "2026-06-25")
    assert id1 == id2
    rows = conn.execute("select name from venues").fetchall()
    assert len(rows) == 1 and rows[0]["name"] == "Bar X (renamed)"


def test_edge_with_serving_and_provenance_roundtrips():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "manual", "2026-06-24", serving="tank")
    out = fetch_venues_with_brands(conn)
    assert out[0]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "manual",
         "serving": "tank", "last_seen": "2026-06-24"}
    ]


def test_delete_edges_removes_stale_links():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "WALD", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "finder:Pilsner Urquell", "2026-06-24", serving="tank")
    assert delete_edges(conn, vid, bid) == 1
    assert fetch_venues_with_brands(conn)[0]["brands"] == []
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.db`.

- [ ] **Step 6: Write `pipeline/db.py`**

```python
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY,
    osm_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    address TEXT,
    website TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS venue_brand (
    venue_id INTEGER NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    serving TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 1.0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (venue_id, brand_id, source)
);
"""


def get_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_venue(conn, venue, seen: str) -> int:
    conn.execute(
        """
        INSERT INTO venues (osm_id, name, lat, lon, address, website, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(osm_id) DO UPDATE SET
            name=excluded.name, lat=excluded.lat, lon=excluded.lon,
            address=excluded.address, website=excluded.website, updated_at=excluded.updated_at
        """,
        (venue.osm_id, venue.name, venue.lat, venue.lon, venue.address, venue.website, seen),
    )
    return conn.execute("SELECT id FROM venues WHERE osm_id=?", (venue.osm_id,)).fetchone()["id"]


def upsert_brand(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO brands (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM brands WHERE name=?", (name,)).fetchone()["id"]


def upsert_edge(conn, venue_id, brand_id, source, seen, serving="unknown", confidence=1.0) -> None:
    conn.execute(
        """
        INSERT INTO venue_brand (venue_id, brand_id, source, serving, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue_id, brand_id, source) DO UPDATE SET
            serving=excluded.serving, last_seen=excluded.last_seen, confidence=excluded.confidence
        """,
        (venue_id, brand_id, source, serving, confidence, seen, seen),
    )


def delete_edges(conn, venue_id, brand_id) -> int:
    cur = conn.execute("DELETE FROM venue_brand WHERE venue_id=? AND brand_id=?", (venue_id, brand_id))
    return cur.rowcount


def fetch_venues_with_brands(conn) -> list[dict]:
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website FROM venues ORDER BY id"
    ).fetchall()
    out = []
    for v in venues:
        edges = conn.execute(
            """
            SELECT b.name AS brand, vb.source AS source, vb.serving AS serving, vb.last_seen AS last_seen
            FROM venue_brand vb JOIN brands b ON b.id = vb.brand_id
            WHERE vb.venue_id = ?
            ORDER BY
                CASE WHEN vb.source='manual' THEN 0
                     WHEN vb.source LIKE 'finder:%' THEN 1 ELSE 2 END,
                b.name
            """,
            (v["id"],),
        ).fetchall()
        out.append({
            "osm_id": v["osm_id"], "name": v["name"], "lat": v["lat"], "lon": v["lon"],
            "address": v["address"], "website": v["website"],
            "brands": [dict(e) for e in edges],
        })
    return out
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pyproject.toml .gitignore pipeline/ tests/test_db.py
git commit -m "feat: project skeleton, config, models, and SQLite data layer with serving + provenance"
```

---

### Task 2: OSM venue + brewery-tag fetch & parse

**Files:**
- Create: `pipeline/osm.py`
- Test: `tests/test_osm.py`

**Interfaces:**
- Consumes: `pipeline.models.{Venue, BrandEdge}`, `pipeline.config.{HAMBURG_QL, OVERPASS_URL, USER_AGENT}`
- Produces: `pipeline.osm.parse_overpass(data:dict)->tuple[list[Venue], list[BrandEdge]]`, `pipeline.osm.fetch_overpass(ql=HAMBURG_QL, url=OVERPASS_URL)->dict`

- [ ] **Step 1: Write the failing test `tests/test_osm.py`**

```python
from pipeline.osm import parse_overpass

SAMPLE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 53.55, "lon": 9.99,
         "tags": {"amenity": "pub", "name": "Zur Quelle", "brewery": "Ratsherrn",
                  "addr:street": "Lange Reihe", "addr:housenumber": "5",
                  "addr:postcode": "20099", "addr:city": "Hamburg",
                  "website": "https://quelle.example"}},
        {"type": "way", "id": 2, "center": {"lat": 53.56, "lon": 10.01},
         "tags": {"amenity": "bar", "name": "Eckkneipe", "brewery": "Astra;Holsten"}},
        {"type": "node", "id": 3, "lat": 53.5, "lon": 10.0, "tags": {"amenity": "cafe"}},
    ]
}


def test_parse_extracts_venues_and_brewery_edges():
    venues, edges = parse_overpass(SAMPLE)
    by_id = {v.osm_id: v for v in venues}
    assert set(by_id) == {"node/1", "way/2"}  # nameless cafe dropped
    assert by_id["node/1"].address == "Lange Reihe 5, 20099 Hamburg"
    assert by_id["way/2"].lat == 53.56  # uses center for ways
    pairs = {(e.venue_osm_id, e.brand) for e in edges}
    assert pairs == {("node/1", "Ratsherrn"), ("way/2", "Astra"), ("way/2", "Holsten")}
    assert all(e.source == "osm" and e.serving == "unknown" for e in edges)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_osm.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.osm`.

- [ ] **Step 3: Write `pipeline/osm.py`**

```python
import httpx

from .config import HAMBURG_QL, OVERPASS_URL, USER_AGENT
from .models import BrandEdge, Venue

_SKIP_BREWERY = {"", "yes", "no", "various", "*", "guest"}


def _coords(el):
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def _address(tags):
    line1 = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p)
    line2 = " ".join(p for p in (tags.get("addr:postcode"), tags.get("addr:city")) if p)
    return ", ".join(p for p in (line1, line2) if p) or None


def _brands_from_tags(tags):
    raw = tags.get("brewery")
    if not raw:
        return []
    return [p.strip() for p in raw.split(";") if p.strip().lower() not in _SKIP_BREWERY]


def parse_overpass(data):
    venues, edges = [], []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        lat, lon = _coords(el)
        if lat is None:
            continue
        osm_id = f"{el['type']}/{el['id']}"
        venues.append(Venue(osm_id, name, lat, lon, _address(tags),
                            tags.get("website") or tags.get("contact:website")))
        for brand in _brands_from_tags(tags):
            edges.append(BrandEdge(osm_id, brand, "osm"))
    return venues, edges


def fetch_overpass(ql: str = HAMBURG_QL, url: str = OVERPASS_URL) -> dict:
    resp = httpx.get(url, params={"data": ql}, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_osm.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Smoke-test the live fetch (manual)**

Run:
```bash
python -c "from pipeline.osm import fetch_overpass, parse_overpass; v,e=parse_overpass(fetch_overpass()); print(len(v),'venues', len(e),'brewery edges')"
```
Expected: a few thousand venues, a few dozen brewery edges. Re-run if Overpass times out (public endpoint is rate-limited).

- [ ] **Step 6: Commit**

```bash
git add pipeline/osm.py tests/test_osm.py
git commit -m "feat: fetch and parse Hamburg venues + brewery tags from OSM"
```

---

### Task 3: Finder framework + Ratsherrn finder (Fassbier seed)

**Files:**
- Create: `pipeline/finders/__init__.py`, `pipeline/finders/base.py`, `pipeline/finders/ratsherrn.py`
- Test: `tests/test_finders.py`

**Interfaces:**
- Consumes: `pipeline.models.FinderEntry`, `pipeline.config.USER_AGENT`
- Produces: `pipeline.finders.base.http_get(url)->str`; `pipeline.finders.base.BaseFinder` with attrs `brand:str`, `url:str`, `serving:str="unknown"` and methods `fetch()->str`, `parse(raw)->list[FinderEntry]`, `run()->list[FinderEntry]`; `pipeline.finders.ratsherrn.RatsherrnFinder`; `pipeline.finders.FINDERS:list[BaseFinder]`

**Note on finders:** parsers are fixture-driven — third-party markup can't be known without fetching it. Recipe: save the live page to a fixture, write the parser against it, test, then confirm live. Finders are **low-trust dated seeds**, not source-of-truth; the curation layer (Task 6) corrects them.

- [ ] **Step 1: Write the failing test `tests/test_finders.py`**

```python
from pipeline.finders.base import BaseFinder
from pipeline.finders.ratsherrn import RatsherrnFinder
from pipeline.models import FinderEntry

FIXTURE = """
<html><body>
  <section><h2>Altes Mädchen</h2><p>Lagerstraße 28b, 20357 Hamburg</p></section>
  <section><h2>Ratsherrn Bar Schanze</h2><p>Lagerstraße 30a, 20357 Hamburg</p></section>
  <section><h2>Dolden Mädel Berlin</h2><p>Skalitzer Straße 25, 10999 Berlin</p></section>
</body></html>
"""


def test_basefinder_run_calls_fetch_then_parse():
    class Fake(BaseFinder):
        brand = "Test"
        def fetch(self):
            return "x"
        def parse(self, raw):
            return [FinderEntry(name="Foo", brand=self.brand)]
    assert Fake().run() == [FinderEntry(name="Foo", brand="Test")]


def test_ratsherrn_parses_hamburg_venues_as_fassbier():
    entries = RatsherrnFinder().parse(FIXTURE)
    assert {e.name for e in entries} == {"Altes Mädchen", "Ratsherrn Bar Schanze"}  # Berlin dropped
    for e in entries:
        assert e.brand == "Ratsherrn"
        assert e.serving == "fass"
        assert "Hamburg" in (e.address or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finders.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.finders.base`.

- [ ] **Step 3: Write `pipeline/finders/base.py`**

```python
import time

import httpx

from ..config import USER_AGENT
from ..models import FinderEntry

_LAST_CALL = {"t": 0.0}
_MIN_INTERVAL = 1.0  # seconds between finder requests


def http_get(url: str) -> str:
    wait = _MIN_INTERVAL - (time.monotonic() - _LAST_CALL["t"])
    if wait > 0:
        time.sleep(wait)
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
    _LAST_CALL["t"] = time.monotonic()
    resp.raise_for_status()
    return resp.text


class BaseFinder:
    brand: str = ""
    url: str = ""
    serving: str = "unknown"

    def fetch(self) -> str:
        return http_get(self.url)

    def parse(self, raw: str) -> list[FinderEntry]:
        raise NotImplementedError

    def run(self) -> list[FinderEntry]:
        return self.parse(self.fetch())
```

- [ ] **Step 4: Write `pipeline/finders/ratsherrn.py`**

```python
import re

from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

_ADDR = re.compile(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß.\- ]+")


class RatsherrnFinder(BaseFinder):
    brand = "Ratsherrn"
    serving = "fass"
    url = "https://ratsherrn.de/ratsherrn-gastro/"

    def parse(self, raw: str) -> list[FinderEntry]:
        soup = BeautifulSoup(raw, "lxml")
        entries: list[FinderEntry] = []
        for heading in soup.find_all(["h2", "h3"]):
            name = heading.get_text(strip=True)
            if not name:
                continue
            block = heading.find_parent(["section", "div"]) or heading.parent
            text = block.get_text(" ", strip=True) if block else ""
            m = _ADDR.search(text)
            if not m or "Hamburg" not in m.group(0):
                continue
            entries.append(FinderEntry(name=name, brand=self.brand,
                                       address=m.group(0).strip(), serving=self.serving))
        return entries
```

- [ ] **Step 5: Write `pipeline/finders/__init__.py`**

```python
from .ratsherrn import RatsherrnFinder

FINDERS = [RatsherrnFinder()]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_finders.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Verify against the live page (manual)**

Run:
```bash
python -c "from pipeline.finders import FINDERS; [print(f.brand, len(f.run())) for f in FINDERS]"
```
Expected: prints `Ratsherrn` and a small count. If 0, inspect the live markup and adjust the selector in `parse`; the fixture test contract stays valid.

- [ ] **Step 8: Commit**

```bash
git add pipeline/finders/ tests/test_finders.py
git commit -m "feat: finder framework with throttled fetch and Ratsherrn (Fassbier) scraper"
```

---

### Task 4: Pilsner Urquell (Tankovna) finder — Tankbier seed

**Files:**
- Create: `pipeline/finders/pilsner_urquell.py`
- Modify: `pipeline/finders/__init__.py`
- Test: `tests/test_finders.py` (add a case)

**Interfaces:**
- Consumes: `BaseFinder`, `FinderEntry`
- Produces: `pipeline.finders.pilsner_urquell.PilsnerUrquellFinder`; updated `FINDERS`

**Note:** the official locator `https://www.pilsnerurquell.com/pubs/` is server-rendered static HTML with a Germany→Hamburg filter and "Load more" pagination; it lists Tankovna (tank) bars, so entries are `serving="tank"`. It is **incomplete and can be stale** (e.g. it may list venues that have switched away) — which is exactly why it's a low-trust seed corrected by `curation.yaml` (Task 6). Verify selectors against the live page.

- [ ] **Step 1: Add a failing test case to `tests/test_finders.py`**

```python
PU_FIXTURE = """
<ul class="pubs">
  <li class="pub"><a href="/pubs/wald/"><h3>WALD</h3></a><span>Hamburg</span></li>
  <li class="pub"><a href="/pubs/gloria/"><h3>Gloria</h3></a><span>Hamburg</span></li>
  <li class="pub"><a href="/pubs/james-june/"><h3>James June</h3></a><span>Berlin</span></li>
</ul>
"""


def test_pilsner_urquell_parses_hamburg_tank_venues():
    from pipeline.finders.pilsner_urquell import PilsnerUrquellFinder
    entries = PilsnerUrquellFinder().parse(PU_FIXTURE)
    assert {e.name for e in entries} == {"WALD", "Gloria"}  # Berlin dropped
    for e in entries:
        assert e.brand == "Pilsner Urquell"
        assert e.serving == "tank"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finders.py::test_pilsner_urquell_parses_hamburg_tank_venues -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.finders.pilsner_urquell`.

- [ ] **Step 3: Write `pipeline/finders/pilsner_urquell.py`**

```python
from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

_CITY = "Hamburg"


class PilsnerUrquellFinder(BaseFinder):
    brand = "Pilsner Urquell"
    serving = "tank"
    url = "https://www.pilsnerurquell.com/pubs/"

    def parse(self, raw: str) -> list[FinderEntry]:
        soup = BeautifulSoup(raw, "lxml")
        seen, entries = set(), []
        for card in soup.select("li, article, .pub"):
            text = card.get_text(" ", strip=True)
            if _CITY not in text:
                continue
            name_el = card.find(["h2", "h3", "h4", "a"])
            name = name_el.get_text(strip=True) if name_el else ""
            if name and name not in seen:
                seen.add(name)
                entries.append(FinderEntry(name=name, brand=self.brand,
                                           address=_CITY, serving=self.serving))
        return entries
```

- [ ] **Step 4: Update `pipeline/finders/__init__.py`**

```python
from .pilsner_urquell import PilsnerUrquellFinder
from .ratsherrn import RatsherrnFinder

FINDERS = [RatsherrnFinder(), PilsnerUrquellFinder()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_finders.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Verify against the live page (manual)**

Run:
```bash
python -c "from pipeline.finders import FINDERS; [print(f.brand, [e.name for e in f.run()]) for f in FINDERS]"
```
Expected: Pilsner Urquell prints its Hamburg tank venues. If the selector over- or under-matches, tighten it against the live markup; the fixture test contract stays valid.

- [ ] **Step 7: Commit**

```bash
git add pipeline/finders/pilsner_urquell.py pipeline/finders/__init__.py tests/test_finders.py
git commit -m "feat: Pilsner Urquell Tankovna finder (Tankbier seed)"
```

---

### Task 5: Matching finder entries to OSM venues

**Files:**
- Create: `pipeline/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `pipeline.models.{Venue, FinderEntry}`
- Produces: `pipeline.matching.haversine_m(lat1,lon1,lat2,lon2)->float`; `match_entry(entry, venues, name_threshold=85, max_dist_m=120)->Venue|None`; `match_entries(entries, venues)->tuple[list[tuple[FinderEntry, Venue]], list[FinderEntry]]`

- [ ] **Step 1: Write the failing test `tests/test_matching.py`**

```python
from pipeline.matching import match_entry, match_entries
from pipeline.models import FinderEntry, Venue

VENUES = [
    Venue("node/10", "Altes Mädchen", 53.5635, 9.9650),
    Venue("node/11", "Irgendeine Kneipe", 53.6000, 10.0500),
]


def test_match_by_fuzzy_name():
    v = match_entry(FinderEntry(name="Altes Maedchen", brand="Ratsherrn"), VENUES)
    assert v is not None and v.osm_id == "node/10"


def test_no_match_returns_none():
    assert match_entry(FinderEntry(name="Völlig Anderer Laden", brand="X"), VENUES) is None


def test_match_entries_splits_matched_and_unmatched():
    entries = [FinderEntry(name="Altes Mädchen", brand="R"),
               FinderEntry(name="Unbekannt", brand="R")]
    matched, unmatched = match_entries(entries, VENUES)
    assert [v.osm_id for _, v in matched] == ["node/10"]
    assert [e.name for e in unmatched] == ["Unbekannt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_matching.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.matching`.

- [ ] **Step 3: Write `pipeline/matching.py`**

```python
from math import asin, cos, radians, sin, sqrt

from rapidfuzz import fuzz

from .models import FinderEntry, Venue


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def match_entry(entry: FinderEntry, venues: list[Venue],
                name_threshold: int = 85, max_dist_m: float = 120) -> Venue | None:
    best, best_score = None, -1.0
    for v in venues:
        if entry.lat is not None and entry.lon is not None:
            if haversine_m(entry.lat, entry.lon, v.lat, v.lon) > max_dist_m:
                continue
        score = fuzz.token_sort_ratio(entry.name.lower(), v.name.lower())
        if score >= name_threshold and score > best_score:
            best, best_score = v, score
    return best


def match_entries(entries, venues):
    matched, unmatched = [], []
    for e in entries:
        v = match_entry(e, venues)
        (matched.append((e, v)) if v else unmatched.append(e))
    return matched, unmatched
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_matching.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add pipeline/matching.py tests/test_matching.py
git commit -m "feat: geo + fuzzy-name matching of finder entries to OSM venues"
```

---

### Task 6: Curation layer — the human-verified core

**Files:**
- Create: `pipeline/curation.py`, `curation.yaml` (seed it with the real corrections you already know)
- Test: `tests/test_curation.py`

**Interfaces:**
- Consumes: `pipeline.db.{upsert_venue, upsert_brand, upsert_edge, delete_edges}`, `pipeline.config.normalize_brand`, `pipeline.models.Venue`
- Produces: `pipeline.curation.load_curation(path)->list[dict]`; `pipeline.curation.apply_curation(conn, entries, venues, today)->dict` returning `{"added":int,"removed":int,"skipped":int}`

**Behavior:** each curation entry resolves a venue (by `osm_id`, else by `lat`/`lon` → create a `manual/<slug>` venue, else by fuzzy name against OSM venues), then `action: add` writes a `source="manual"` edge (highest trust) or `action: remove` deletes every edge for that venue+brand (kills stale finder/OSM links). `serving` and `verified` (date) come from the entry.

- [ ] **Step 1: Write `curation.yaml` (seed with known truth)**

```yaml
# Hand-curated venue<->brand truth. Highest trust. Each entry adds or removes
# one link. Resolve a venue by `osm_id`, or by `lat`+`lon` (creates the venue),
# or by `venue` name (fuzzy-matched to OSM). `verified` is the date you checked.
- venue: WALD
  brand: Pilsner Urquell
  action: remove
  verified: 2026-06-24
  note: "WALD switched to Budweiser Budvar tank; no longer serves PU."

- venue: WALD
  brand: Budweiser Budvar
  serving: tank
  action: add
  verified: 2026-06-24

- venue: The Irishman
  brand: Pilsner Urquell
  serving: fass
  action: add
  verified: 2026-06-24

- venue: Pampa
  lat: 53.5556
  lon: 9.9636
  brand: Pilsner Urquell
  serving: tank
  action: add
  verified: 2026-06-24
  note: "Coords approximate — correct if needed."
```
(Adjust names/coords to match reality; this file is the project's living dataset.)

- [ ] **Step 2: Write the failing test `tests/test_curation.py`**

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_curation.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.curation`.

- [ ] **Step 4: Write `pipeline/curation.py`**

```python
import re

import yaml
from rapidfuzz import fuzz

from .config import normalize_brand
from .db import delete_edges, upsert_brand, upsert_edge, upsert_venue
from .models import Venue


def load_curation(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or []
    except FileNotFoundError:
        return []


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _resolve_venue_id(conn, entry, venues, today):
    if entry.get("osm_id"):
        row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (entry["osm_id"],)).fetchone()
        return row["id"] if row else None
    if entry.get("lat") is not None and entry.get("lon") is not None:
        osm_id = "manual/" + _slug(entry["venue"])
        return upsert_venue(conn, Venue(osm_id, entry["venue"], entry["lat"], entry["lon"]), today)
    best, best_score = None, -1.0
    for v in venues:
        score = fuzz.token_sort_ratio(entry["venue"].lower(), v.name.lower())
        if score >= 85 and score > best_score:
            best, best_score = v, score
    if best is None:
        return None
    row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (best.osm_id,)).fetchone()
    return row["id"] if row else None


def apply_curation(conn, entries, venues, today) -> dict:
    counts = {"added": 0, "removed": 0, "skipped": 0}
    for entry in entries:
        vid = _resolve_venue_id(conn, entry, venues, today)
        if vid is None:
            counts["skipped"] += 1
            continue
        bid = upsert_brand(conn, normalize_brand(entry["brand"]))
        if entry.get("action", "add") == "remove":
            delete_edges(conn, vid, bid)
            counts["removed"] += 1
        else:
            upsert_edge(conn, vid, bid, "manual", entry.get("verified") or today,
                        serving=entry.get("serving", "unknown"))
            counts["added"] += 1
    return counts
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_curation.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add pipeline/curation.py curation.yaml tests/test_curation.py
git commit -m "feat: human-curated manual layer (add/remove, highest trust) with seed data"
```

---

### Task 7: Export SQLite to GeoJSON

**Files:**
- Create: `pipeline/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `pipeline.db.fetch_venues_with_brands`
- Produces: `pipeline.export.export_geojson(conn, out_path)->int` (feature count)

- [ ] **Step 1: Write the failing test `tests/test_export.py`**

```python
import json

from pipeline.db import get_connection, init_db, upsert_brand, upsert_edge, upsert_venue
from pipeline.export import export_geojson
from pipeline.models import Venue


def test_export_writes_geojson_with_brand_provenance(tmp_path):
    conn = get_connection(":memory:")
    init_db(conn)
    vid = upsert_venue(conn, Venue("manual/pampa", "Pampa", 53.5556, 9.9636,
                                   address="Hamburg"), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "manual", "2026-06-24", serving="tank")

    out = tmp_path / "venues.json"
    assert export_geojson(conn, str(out)) == 1
    fc = json.loads(out.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [9.9636, 53.5556]  # lon, lat
    assert feat["properties"]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "manual", "serving": "tank",
         "last_seen": "2026-06-24"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.export`.

- [ ] **Step 3: Write `pipeline/export.py`**

```python
import json
from pathlib import Path

from .db import fetch_venues_with_brands


def export_geojson(conn, out_path: str) -> int:
    rows = fetch_venues_with_brands(conn)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {
            "name": r["name"], "address": r["address"],
            "website": r["website"], "brands": r["brands"],
        },
    } for r in rows]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False), encoding="utf-8")
    return len(features)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_export.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add pipeline/export.py tests/test_export.py
git commit -m "feat: export venues + brand provenance to GeoJSON"
```

---

### Task 8: Pipeline orchestration (cron entrypoint)

**Files:**
- Create: `pipeline/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: everything above + `pipeline.config.{DB_PATH, OUT_PATH, CURATION_PATH, normalize_brand}`
- Produces: `pipeline.run.run_pipeline(db_path=DB_PATH, out_path=OUT_PATH, curation_path=CURATION_PATH, overpass_fetch=osm.fetch_overpass, finders=FINDERS, today=None)->dict` returning `{"venues","osm_edges","finder_edges","unmatched","manual_added","manual_removed","exported"}`. Runs via `python -m pipeline.run`. **Order: OSM → finders → curation (last) → export.**

- [ ] **Step 1: Write the failing test `tests/test_run.py`**

```python
import json

from pipeline.finders.base import BaseFinder
from pipeline.models import FinderEntry
from pipeline.run import run_pipeline

FAKE_OVERPASS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 53.5635, "lon": 9.9650,
         "tags": {"amenity": "pub", "name": "Altes Mädchen", "brewery": "Holsten"}},
        {"type": "node", "id": 2, "lat": 53.55, "lon": 9.97,
         "tags": {"amenity": "bar", "name": "WALD"}},
    ]
}


class FakeFinder(BaseFinder):
    brand = "Pilsner Urquell"
    serving = "tank"
    def run(self):
        return [FinderEntry(name="WALD", brand="Pilsner Urquell", serving="tank")]


def test_run_pipeline_applies_curation_over_finders(tmp_path):
    out = tmp_path / "venues.json"
    cur = tmp_path / "curation.yaml"
    cur.write_text(
        "- venue: WALD\n  brand: Pilsner Urquell\n  action: remove\n  verified: 2026-06-24\n"
        "- venue: WALD\n  brand: budvar\n  serving: tank\n  action: add\n  verified: 2026-06-24\n",
        encoding="utf-8",
    )
    summary = run_pipeline(
        db_path=":memory:", out_path=str(out), curation_path=str(cur),
        overpass_fetch=lambda: FAKE_OVERPASS, finders=[FakeFinder()], today="2026-06-24",
    )
    assert summary == {
        "venues": 2, "osm_edges": 1, "finder_edges": 1,
        "unmatched": 0, "manual_added": 1, "manual_removed": 1, "exported": 2,
    }
    fc = json.loads(out.read_text(encoding="utf-8"))
    by_name = {f["properties"]["name"]: f["properties"]["brands"] for f in fc["features"]}
    # Finder added PU@WALD, curation removed it and added Budvar -> only Budvar remains.
    assert by_name["WALD"] == [{"brand": "Budweiser Budvar", "source": "manual",
                                "serving": "tank", "last_seen": "2026-06-24"}]
    assert {b["brand"] for b in by_name["Altes Mädchen"]} == {"Holsten"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: pipeline.run`.

- [ ] **Step 3: Write `pipeline/run.py`**

```python
from datetime import date

from . import curation, osm
from .config import CURATION_PATH, DB_PATH, OUT_PATH, normalize_brand
from .db import get_connection, init_db, upsert_brand, upsert_edge, upsert_venue
from .export import export_geojson
from .finders import FINDERS
from .matching import match_entries


def _store_edge(conn, venue_id, brand, source, seen, serving="unknown"):
    bid = upsert_brand(conn, normalize_brand(brand))
    upsert_edge(conn, venue_id, bid, source, seen, serving=serving)


def run_pipeline(db_path=DB_PATH, out_path=OUT_PATH, curation_path=CURATION_PATH,
                 overpass_fetch=osm.fetch_overpass, finders=FINDERS, today=None):
    today = today or date.today().isoformat()
    conn = get_connection(db_path)
    init_db(conn)

    venues, osm_edges = osm.parse_overpass(overpass_fetch())
    id_by_osm = {v.osm_id: upsert_venue(conn, v, today) for v in venues}
    for e in osm_edges:
        _store_edge(conn, id_by_osm[e.venue_osm_id], e.brand, e.source, today, serving=e.serving)

    finder_edges = unmatched_total = 0
    for f in finders:
        matched, unmatched = match_entries(f.run(), venues)
        unmatched_total += len(unmatched)
        for entry, venue in matched:
            _store_edge(conn, id_by_osm[venue.osm_id], entry.brand,
                        f"finder:{f.brand}", today, serving=entry.serving)
            finder_edges += 1

    cur = curation.apply_curation(conn, curation.load_curation(curation_path), venues, today)
    conn.commit()
    exported = export_geojson(conn, out_path)
    return {
        "venues": len(venues), "osm_edges": len(osm_edges), "finder_edges": finder_edges,
        "unmatched": unmatched_total, "manual_added": cur["added"],
        "manual_removed": cur["removed"], "exported": exported,
    }


if __name__ == "__main__":
    print(run_pipeline())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Full suite + real end-to-end build (manual)**

Run:
```bash
pytest -v
python -m pipeline.run
```
Expected: all tests pass; the real run prints a summary dict (note `manual_added`/`manual_removed` reflecting `curation.yaml`) and writes `web/data/venues.json`. Requires network for Overpass + finders.

- [ ] **Step 6: Commit**

```bash
git add pipeline/run.py tests/test_run.py
git commit -m "feat: end-to-end pipeline (OSM -> finders -> curation -> export)"
```

---

### Task 9: Frontend — DataSource + MapLibre map with brand & serving filters

**Files:**
- Create: `web/datasource.js`, `web/datasource.test.js`, `web/index.html`, `web/style.css`, `web/app.js`, `web/data/.gitkeep`, `web/vendor/` (downloaded assets)
- Test: `web/datasource.test.js` (`node --test`)

**Interfaces:**
- Produces (`web/datasource.js`, ES modules), where `Venue = {name, lat, lon, address, website, brands: Brand[]}` and `Brand = {brand, source, serving, last_seen}`:
  - `loadVenues(featureCollection)->Venue[]` (brands sorted by trust: manual < finder < osm)
  - `buildBrandList(venues)->string[]` (unique, sorted)
  - `venuesByBrand(venues, brand, serving=null)->Venue[]`
  - `venuesByServing(venues, serving)->Venue[]`

- [ ] **Step 1: Write the failing test `web/datasource.test.js`**

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

const FC = {
  type: "FeatureCollection",
  features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [9.99, 53.55] },
      properties: { name: "Bar A", address: "HH", website: null, brands: [
        { brand: "Astra", source: "osm", serving: "unknown", last_seen: "2026-06-24" },
        { brand: "Ratsherrn", source: "manual", serving: "fass", last_seen: "2026-06-24" } ] } },
    { type: "Feature", geometry: { type: "Point", coordinates: [10.05, 53.60] },
      properties: { name: "WALD", address: "HH", website: null, brands: [
        { brand: "Budweiser Budvar", source: "manual", serving: "tank", last_seen: "2026-06-24" } ] } },
  ],
};

test("loadVenues flattens and sorts brands by trust (manual first)", () => {
  const v = loadVenues(FC);
  assert.equal(v[0].lat, 53.55);
  assert.equal(v[0].brands[0].source, "manual");  // Ratsherrn before Astra
});

test("buildBrandList is unique + sorted", () => {
  assert.deepEqual(buildBrandList(loadVenues(FC)), ["Astra", "Budweiser Budvar", "Ratsherrn"]);
});

test("venuesByBrand optionally filters by serving", () => {
  const v = loadVenues(FC);
  assert.deepEqual(venuesByBrand(v, "Ratsherrn").map((x) => x.name), ["Bar A"]);
  assert.deepEqual(venuesByBrand(v, "Ratsherrn", "tank").map((x) => x.name), []);
});

test("venuesByServing filters tank vs fass", () => {
  assert.deepEqual(venuesByServing(loadVenues(FC), "tank").map((x) => x.name), ["WALD"]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test web/`
Expected: FAIL — cannot find module `./datasource.js`.

- [ ] **Step 3: Write `web/datasource.js`**

```javascript
function rank(source) {
  if (source === "manual") return 0;
  if (source.startsWith("finder:")) return 1;
  return 2;
}

export function loadVenues(fc) {
  return (fc.features || []).map((f) => ({
    name: f.properties.name,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    address: f.properties.address,
    website: f.properties.website,
    brands: [...(f.properties.brands || [])].sort(
      (a, b) => rank(a.source) - rank(b.source) || a.brand.localeCompare(b.brand, "de")),
  }));
}

export function buildBrandList(venues) {
  const set = new Set();
  for (const v of venues) for (const b of v.brands) set.add(b.brand);
  return [...set].sort((a, b) => a.localeCompare(b, "de"));
}

export function venuesByBrand(venues, brand, serving = null) {
  return venues.filter((v) =>
    v.brands.some((b) => b.brand === brand && (!serving || b.serving === serving)));
}

export function venuesByServing(venues, serving) {
  return venues.filter((v) => v.brands.some((b) => b.serving === serving));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test web/`
Expected: PASS (4 tests).

- [ ] **Step 5: Download self-hosted MapLibre assets**

Run:
```bash
mkdir -p web/vendor web/data && touch web/data/.gitkeep
curl -L -o web/vendor/maplibre-gl.js  https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js
curl -L -o web/vendor/maplibre-gl.css https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css
```
Expected: two non-empty files in `web/vendor/`.

- [ ] **Step 6: Write `web/style.css`**

```css
html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; }
#map { position: absolute; inset: 0; }
#panel { position: absolute; top: 10px; left: 10px; z-index: 1; background: #fff;
         padding: 12px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,.3); max-width: 260px; }
#panel h1 { font-size: 16px; margin: 0 0 8px; }
#panel label { display: block; margin-top: 8px; font-size: 13px; }
select { width: 100%; padding: 6px; }
.badge { display: inline-block; font-size: 11px; background: #eee; border-radius: 4px;
         padding: 1px 5px; margin-left: 4px; color: #444; }
.badge.manual { background: #d9f2e3; color: #1c6b3c; }
footer { position: absolute; bottom: 4px; right: 6px; z-index: 1; font-size: 11px;
         background: rgba(255,255,255,.85); padding: 2px 6px; border-radius: 4px; }
```

- [ ] **Step 7: Write `web/index.html`**

```html
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bier vom Fass – Hamburg</title>
  <link rel="stylesheet" href="vendor/maplibre-gl.css" />
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <div id="map"></div>
  <div id="panel">
    <h1>Bier vom Fass – Hamburg</h1>
    <label for="brand">Marke</label>
    <select id="brand"><option value="">Alle Marken</option></select>
    <label for="serving">Ausschank</label>
    <select id="serving">
      <option value="">Fass &amp; Tank</option>
      <option value="fass">nur Fassbier</option>
      <option value="tank">nur Tankbier</option>
    </select>
    <p id="count"></p>
  </div>
  <footer>
    © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>
    · <a href="impressum.html">Impressum</a> · <a href="datenschutz.html">Datenschutz</a>
  </footer>
  <script src="vendor/maplibre-gl.js"></script>
  <script type="module" src="app.js"></script>
</body>
</html>
```

- [ ] **Step 8: Write `web/app.js`**

```javascript
import { loadVenues, buildBrandList, venuesByBrand, venuesByServing } from "./datasource.js";

const SERVING_LABEL = { tank: "Tankbier", fass: "Fassbier", unknown: "" };
const SOURCE_LABEL = { manual: "✓ verifiziert", osm: "OSM" };

const OSM_STYLE = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                    tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const map = new maplibregl.Map({ container: "map", style: OSM_STYLE, center: [9.9937, 53.5511], zoom: 12 });
const brandSelect = document.getElementById("brand");
const servingSelect = document.getElementById("serving");
const countEl = document.getElementById("count");
let allVenues = [];

function toFC(venues) {
  return { type: "FeatureCollection", features: venues.map((v) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [v.lon, v.lat] },
    properties: { html: `<strong>${v.name}</strong><br>${v.address || ""}<br>` + v.brands.map((b) => {
      const parts = [SERVING_LABEL[b.serving], SOURCE_LABEL[b.source] || b.source, b.last_seen].filter(Boolean);
      const cls = b.source === "manual" ? "badge manual" : "badge";
      return `${b.brand}<span class="${cls}">${parts.join(" · ")}</span>`;
    }).join("<br>") } })) };
}

function render(venues) {
  map.getSource("venues").setData(toFC(venues));
  countEl.textContent = `${venues.length} Orte`;
}

function applyFilters() {
  const b = brandSelect.value, s = servingSelect.value;
  let r = allVenues;
  if (b) r = venuesByBrand(r, b, s || null);
  else if (s) r = venuesByServing(r, s);
  render(r);
}

map.on("load", async () => {
  allVenues = loadVenues(await (await fetch("data/venues.json")).json());
  for (const brand of buildBrandList(allVenues)) {
    const o = document.createElement("option");
    o.value = o.textContent = brand;
    brandSelect.appendChild(o);
  }
  map.addSource("venues", { type: "geojson", data: toFC(allVenues) });
  map.addLayer({ id: "dots", type: "circle", source: "venues",
    paint: { "circle-radius": 6, "circle-color": "#c8102e", "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
  render(allVenues);
  map.on("click", "dots", (e) => new maplibregl.Popup().setLngLat(e.lngLat).setHTML(e.features[0].properties.html).addTo(map));
  map.on("mouseenter", "dots", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "dots", () => (map.getCanvas().style.cursor = ""));
  brandSelect.addEventListener("change", applyFilters);
  servingSelect.addEventListener("change", applyFilters);
});
```

- [ ] **Step 9: Manual test the map**

Run (after `python -m pipeline.run` produced `web/data/venues.json`):
```bash
python -m http.server -d web 8000
```
Open `http://localhost:8000`. Verify: Hamburg map with red dots; brand + serving dropdowns populated; selecting a brand and/or `nur Tankbier` narrows the dots and updates the count; clicking a dot lists each brand with a serving label and a source badge (manual links show a green "✓ verifiziert" badge and sort first); OSM attribution + Impressum/Datenschutz links visible; no network requests to unpkg (assets load from `vendor/`).

- [ ] **Step 10: Commit**

```bash
git add web/datasource.js web/datasource.test.js web/index.html web/style.css web/app.js web/vendor/ web/data/.gitkeep
git commit -m "feat: MapLibre frontend with brand + serving filters and trust-ranked provenance"
```

---

### Task 10: Legal pages, README, and cron wiring

**Files:**
- Create: `web/impressum.html`, `web/datenschutz.html`, `README.md`

**Note:** Impressum/Datenschutz contain the operator's real details — genuine user-supplied values, marked clearly to fill in.

- [ ] **Step 1: Write `web/impressum.html`**

```html
<!doctype html>
<html lang="de"><head><meta charset="utf-8" /><title>Impressum</title>
<link rel="stylesheet" href="style.css" /></head>
<body style="padding:20px;max-width:700px;margin:auto">
  <h1>Impressum</h1>
  <p>Angaben gemäß § 5 DDG:</p>
  <p><!-- FILL IN: name --><br><!-- FILL IN: Straße + Nr. --><br><!-- FILL IN: PLZ + Ort --></p>
  <h2>Kontakt</h2><p>E-Mail: <!-- FILL IN: email --></p>
  <h2>Verantwortlich für den Inhalt</h2><p><!-- FILL IN: name + Anschrift --></p>
  <p style="margin-top:30px"><a href="index.html">← Zur Karte</a></p>
</body></html>
```

- [ ] **Step 2: Write `web/datenschutz.html`**

```html
<!doctype html>
<html lang="de"><head><meta charset="utf-8" /><title>Datenschutzerklärung</title>
<link rel="stylesheet" href="style.css" /></head>
<body style="padding:20px;max-width:700px;margin:auto">
  <h1>Datenschutzerklärung</h1>
  <h2>Verantwortlicher</h2><p><!-- FILL IN: name + Kontakt --></p>
  <h2>Hosting / Server-Logs</h2>
  <p>Beim Aufruf verarbeitet der Hoster (<!-- FILL IN: Hoster -->) technisch notwendige Daten
     (IP-Adresse, Zeitpunkt, abgerufene Datei). Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO.</p>
  <h2>Kartenmaterial (OpenStreetMap)</h2>
  <p>Die Karte lädt Kartenkacheln von tile.openstreetmap.org; dabei wird Ihre IP-Adresse an die
     OpenStreetMap Foundation übertragen (<a href="https://wiki.osmfoundation.org/wiki/Privacy_Policy">OSMF Privacy Policy</a>).
     Karten-Software (MapLibre) wird von diesem Server ausgeliefert, nicht von Dritten.</p>
  <h2>Keine Tracking-Cookies</h2>
  <p>Diese Website setzt keine Analyse- oder Tracking-Cookies und speichert keine personenbezogenen Besucherdaten.</p>
  <p style="margin-top:30px"><a href="index.html">← Zur Karte</a></p>
</body></html>
```

- [ ] **Step 3: Write `README.md`**

````markdown
# beer-map

A map of Hamburg drinking venues, filterable by draft beer brand and serving type
(Fassbier/Tankbier). Built on a human-curated core (`curation.yaml`, highest trust),
seeded by OpenStreetMap `brewery=` tags and brand "where to drink" finders. Every
venue↔brand link records its source and last-verified date.

See `docs/specs/` (design) and `docs/plans/` (build plan).

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Build the dataset
```bash
python -m pipeline.run        # OSM -> finders -> curation -> web/data/venues.json
```

## Curate (this is the real work)
Edit `curation.yaml` to add/remove venue↔brand links — the highest-trust layer.
`action: add` with `serving: fass|tank` and a `verified:` date; `action: remove`
retires a stale link (e.g. a venue that switched breweries). Re-run the pipeline.

## Serve locally
```bash
python -m http.server -d web 8000   # http://localhost:8000
```

## Tests
```bash
pytest -v          # pipeline
node --test web/   # frontend pure functions
```

## Cron (Raspberry Pi)
```cron
0 4 * * * cd /home/pi/beer-map && /home/pi/beer-map/.venv/bin/python -m pipeline.run >> pipeline.log 2>&1
```

## Roadmap
- **Post-launch:** venue-menu LLM scraping as a coverage booster (new low-trust source under curation).
- **Scaling to Germany:** widen `HAMBURG_QL` to a Germany area; swap `web/datasource.js` to a bbox/brand API — the map code is unchanged.

## Adding a brand finder
1. Save the brand's live "where to drink" page; inspect its structure.
2. Add `pipeline/finders/<brand>.py` subclassing `BaseFinder` (set `serving`).
3. Register it in `pipeline/finders/__init__.py`; add a fixture test.
````

- [ ] **Step 4: Commit**

```bash
git add web/impressum.html web/datenschutz.html README.md
git commit -m "docs: legal pages, README, and cron wiring"
```

---

## Self-Review Notes (completed by plan author)

- **Spec coverage:** OSM venues + `brewery=` seeds (Task 2) ✓; finders as low-trust dated seeds — Ratsherrn/Fass (Task 3), Pilsner Urquell/Tank (Task 4) ✓; matching (Task 5) ✓; **curated human-verified core with add/remove + staleness fix** (Task 6) ✓; serving (Fass/Tank) through models→db→export→frontend ✓; GeoJSON export (Task 7) ✓; pipeline order OSM→finders→curation→export (Task 8) ✓; MapLibre map + brand & serving filters + trust-ranked provenance, self-hosted assets (Task 9) ✓; legal/attribution (Tasks 9–10) ✓. Deferred per decision: venue-menu LLM scraping, public submission form, Germany serving API.
- **Type consistency:** `Venue`/`BrandEdge`/`FinderEntry` (with `serving`) stable across Tasks 1–8; `fetch_venues_with_brands` returns `{name,lat,lon,address,website,brands:[{brand,source,serving,last_seen}]}` consumed identically by `export_geojson` (Task 7) and `datasource.js` (Task 9). Trust order `manual>finder:*>osm` is applied in SQL (`fetch_venues_with_brands`) and re-applied client-side in `loadVenues`.
- **Known judgment calls:** finder parsers are fixture-driven (selectors verified live in Tasks 3/4). Unmatched finder entries are counted and dropped (no auto-geocoding); curation entries for non-OSM venues must supply `lat`/`lon` (e.g. Pampa) or an `osm_id`. `curation.yaml` is committed (it is the dataset), not gitignored.
