from __future__ import annotations

import re
import sqlite3
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY,
    osm_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    address TEXT,
    website TEXT,
    opening_hours TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
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
    beer TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    -- `beer` (the specific product) is part of the key so a venue can list
    -- several beers of the same brand (e.g. Augustiner Edelstoff + Hell). '' =
    -- brand-only (no specific beer).
    PRIMARY KEY (venue_id, brand_id, source, beer)
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    venue_osm_id TEXT,
    venue_name TEXT NOT NULL,
    lat REAL, lon REAL,
    brand TEXT NOT NULL DEFAULT '',
    serving TEXT NOT NULL DEFAULT 'unknown',
    beer TEXT,
    address TEXT,
    opening_hours TEXT,
    note TEXT,
    submitter_ip TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
-- One row per fetched tile of the nationwide sweep (pipeline/country.py), so
-- an interrupted run can resume without refetching what it already has.
CREATE TABLE IF NOT EXISTS country_tiles (
    tile TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    venues INTEGER NOT NULL
);
-- The /api/gray tile endpoint answers viewport queries straight from this
-- table; the composite index turns them into a narrow lon-range scan.
CREATE INDEX IF NOT EXISTS idx_venues_lon_lat ON venues(lon, lat);
"""

# Columns added after the initial schema shipped. Existing databases were created
# with CREATE TABLE IF NOT EXISTS, so they keep the old layout — add them by hand.
_MIGRATIONS = (
    ("venues", "hidden", "INTEGER NOT NULL DEFAULT 0"),
    ("venues", "opening_hours", "TEXT"),
    ("venues", "search_key", "TEXT"),
    ("venue_brand", "beer", "TEXT"),
    ("submissions", "address", "TEXT"),
    ("submissions", "beer", "TEXT"),
    ("submissions", "opening_hours", "TEXT"),
)


def fold(s: str | None) -> str:
    """Search-folded form of a name/address: lowercase, ß -> ss, diacritics
    stripped, punctuation runs collapsed to single spaces. MUST mirror `fold`
    in web/datasource.js — the /api/search endpoint and the client-side search
    compare against the same folded strings."""
    s = (s or "").lower().replace("ß", "ss")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _search_key(name: str | None, address: str | None) -> str:
    return fold(f"{name or ''} {address or ''}")


def get_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn) -> None:
    conn.executescript(SCHEMA)
    for table, column, decl in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    _migrate_venue_brand_pk(conn)
    scrub_plaintext_ips(conn)
    _backfill_search_keys(conn)
    conn.commit()


def _backfill_search_keys(conn) -> int:
    """Fill `search_key` on rows written before the column existed. Guarded by
    a cheap probe so the common (already-filled) case costs one SELECT."""
    if conn.execute(
            "SELECT 1 FROM venues WHERE search_key IS NULL LIMIT 1").fetchone() is None:
        return 0
    rows = conn.execute(
        "SELECT id, name, address FROM venues WHERE search_key IS NULL").fetchall()
    conn.executemany(
        "UPDATE venues SET search_key=? WHERE id=?",
        [(_search_key(r["name"], r["address"]), r["id"]) for r in rows])
    return len(rows)


def scrub_plaintext_ips(conn) -> int:
    """Blank any `submitter_ip` still holding a raw address.

    Rows written before `submissions.hash_ip` existed stored the IP verbatim.
    Clearing them costs nothing — the value is only read by the rate limiter,
    whose window is an hour — and leaves no plaintext address on disk. Guarded
    by a SELECT so the common (already-clean) case is a cheap no-op.
    """
    stale = "submitter_ip IS NOT NULL AND submitter_ip <> '' AND submitter_ip NOT LIKE 'h:%'"
    if conn.execute(f"SELECT 1 FROM submissions WHERE {stale} LIMIT 1").fetchone() is None:
        return 0
    cur = conn.execute(f"UPDATE submissions SET submitter_ip='' WHERE {stale}")
    return cur.rowcount


def _migrate_venue_brand_pk(conn) -> None:
    """Move `beer` into the venue_brand primary key on databases created with the
    old (venue_id, brand_id, source) key, so multiple beers of one brand per
    venue can coexist. Rebuilds the table once; idempotent and data-preserving."""
    info = conn.execute("PRAGMA table_info(venue_brand)").fetchall()
    beer = next((r for r in info if r["name"] == "beer"), None)
    if beer is None or beer["pk"] != 0:
        return  # fresh schema already has beer in the PK
    conn.execute("ALTER TABLE venue_brand RENAME TO _vb_old")
    conn.executescript(SCHEMA)  # recreates venue_brand with the new PK
    conn.execute(
        "INSERT INTO venue_brand "
        "(venue_id, brand_id, source, serving, beer, confidence, first_seen, last_seen) "
        "SELECT venue_id, brand_id, source, serving, COALESCE(beer, ''), "
        "confidence, first_seen, last_seen FROM _vb_old"
    )
    conn.execute("DROP TABLE _vb_old")


def upsert_venue(conn, venue, seen: str) -> int:
    conn.execute(
        """
        INSERT INTO venues (osm_id, name, lat, lon, address, website, opening_hours,
                            search_key, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(osm_id) DO UPDATE SET
            name=excluded.name, lat=excluded.lat, lon=excluded.lon,
            address=excluded.address, website=excluded.website,
            opening_hours=excluded.opening_hours, search_key=excluded.search_key,
            updated_at=excluded.updated_at
        """,
        (venue.osm_id, venue.name, venue.lat, venue.lon, venue.address, venue.website,
         venue.opening_hours, _search_key(venue.name, venue.address), seen),
    )
    return conn.execute("SELECT id FROM venues WHERE osm_id=?", (venue.osm_id,)).fetchone()["id"]


def upsert_brand(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO brands (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM brands WHERE name=?", (name,)).fetchone()["id"]


def upsert_edge(conn, venue_id, brand_id, source, seen, serving="unknown",
                confidence=1.0, beer=None) -> None:
    # `beer` is the optional specific product (e.g. "Edelstoff"); '' means
    # brand-only. It is part of the key, so the same brand can appear several
    # times at one venue with different beers.
    conn.execute(
        """
        INSERT INTO venue_brand (venue_id, brand_id, source, serving, beer, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue_id, brand_id, source, beer) DO UPDATE SET
            serving=excluded.serving, last_seen=excluded.last_seen, confidence=excluded.confidence
        """,
        (venue_id, brand_id, source, serving, beer or "", confidence, seen, seen),
    )


def renormalize_brands(conn, normalize, skip=frozenset()) -> int:
    """Re-apply brand normalization to rows already in the DB. Ingest runs
    `normalize_brand` on new edges only, so rows written before an alias (or
    split rule) existed keep their old spelling forever — this folds them:
    multi-brand names ("A,B") are split, each part normalized, edges remapped
    onto the canonical brand, junk (`skip`) and orphaned brands deleted.
    Returns the number of brand rows folded away."""
    folded = 0
    for row in conn.execute("SELECT id, name FROM brands").fetchall():
        parts = [p.strip() for p in re.split(r"[;,]", row["name"]) if p.strip()]
        targets = [normalize(p) for p in parts if p.lower() not in skip]
        if targets == [row["name"]]:
            continue
        target_ids = {upsert_brand(conn, t) for t in targets}
        for tid in target_ids - {row["id"]}:
            conn.execute(
                """
                INSERT INTO venue_brand
                    (venue_id, brand_id, source, serving, beer, confidence, first_seen, last_seen)
                SELECT venue_id, ?, source, serving, COALESCE(beer, ''), confidence, first_seen, last_seen
                FROM venue_brand WHERE brand_id = ?
                ON CONFLICT(venue_id, brand_id, source, beer) DO UPDATE SET
                    -- keep the existing edge, but don't lose a known serving to 'unknown'
                    serving = CASE WHEN venue_brand.serving = 'unknown'
                                   THEN excluded.serving ELSE venue_brand.serving END,
                    last_seen = MAX(venue_brand.last_seen, excluded.last_seen)
                """,
                (tid, row["id"]),
            )
        if row["id"] not in target_ids:
            conn.execute("DELETE FROM venue_brand WHERE brand_id=?", (row["id"],))
            conn.execute("DELETE FROM brands WHERE id=?", (row["id"],))
            folded += 1
    # Brands with no venue left (e.g. junk-only or fully remapped) would still
    # show up in /api/brands — drop them.
    conn.execute(
        "DELETE FROM brands WHERE id NOT IN (SELECT DISTINCT brand_id FROM venue_brand)")
    return folded


def delete_edges(conn, venue_id, brand_id, beer=None) -> int:
    # beer=None removes every beer of the brand; a specific beer removes just it.
    if beer is None:
        cur = conn.execute(
            "DELETE FROM venue_brand WHERE venue_id=? AND brand_id=?", (venue_id, brand_id))
    else:
        cur = conn.execute(
            "DELETE FROM venue_brand WHERE venue_id=? AND brand_id=? AND beer=?",
            (venue_id, brand_id, beer))
    return cur.rowcount


def update_venue_address(conn, osm_id: str, address: str,
                          lat: float | None = None, lon: float | None = None) -> int:
    row = conn.execute("SELECT name FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    key = _search_key(row["name"] if row else None, address)
    if lat is not None and lon is not None:
        cur = conn.execute(
            "UPDATE venues SET address=?, lat=?, lon=?, search_key=? WHERE osm_id=?",
            (address, lat, lon, key, osm_id))
    else:
        cur = conn.execute("UPDATE venues SET address=?, search_key=? WHERE osm_id=?",
                           (address, key, osm_id))
    return cur.rowcount


def update_venue_hours(conn, osm_id: str, hours: str) -> int:
    """Set a venue's `opening_hours`, overriding what OSM imported.

    OSM is otherwise the only writer of this column (see osm.py), so an approved
    community correction would be undone by the next nightly import. It survives
    because submissions.apply_approved re-runs every approved submission after
    the import, exactly as edit_venue and close_venue already do.

    The weekly country sweep also upserts venues and does not re-apply, so it
    leaves this column holding OSM's value until the next nightly build. That
    is invisible today — hours reach the frontend through the GeoJSON export,
    which only ever runs after apply_approved — but a reader of this column
    added between those two runs would see the stale value.
    """
    cur = conn.execute("UPDATE venues SET opening_hours=? WHERE osm_id=?",
                       (hours or None, osm_id))
    return cur.rowcount


def set_venue_hidden(conn, osm_id: str, hidden: bool) -> int:
    cur = conn.execute("UPDATE venues SET hidden=? WHERE osm_id=?", (1 if hidden else 0, osm_id))
    return cur.rowcount


def _venue_brands(conn, venue_id: int) -> list[dict]:
    edges = conn.execute(
        """
        SELECT b.name AS brand, vb.source AS source, vb.serving AS serving,
               vb.beer AS beer, vb.last_seen AS last_seen
        FROM venue_brand vb JOIN brands b ON b.id = vb.brand_id
        WHERE vb.venue_id = ?
        ORDER BY
            CASE vb.source
                 WHEN 'manual' THEN 0
                 WHEN 'community' THEN 1
                 ELSE CASE WHEN vb.source LIKE 'finder:%' THEN 2 ELSE 3 END END,
            b.name, vb.beer
        """,
        (venue_id,),
    ).fetchall()
    brands = []
    for e in edges:
        d = dict(e)
        d["beer"] = d["beer"] or None  # '' (brand-only) -> null in the export
        brands.append(d)
    return brands


def _venue_dict(conn, v, with_brands: bool = True) -> dict:
    return {
        "osm_id": v["osm_id"], "name": v["name"], "lat": v["lat"], "lon": v["lon"],
        "address": v["address"], "website": v["website"],
        "opening_hours": v["opening_hours"],
        "brands": _venue_brands(conn, v["id"]) if with_brands else [],
    }


_HAS_EDGE = "EXISTS (SELECT 1 FROM venue_brand vb WHERE vb.venue_id = venues.id)"


def fetch_venues_with_brands(conn, branded_only: bool = False) -> list[dict]:
    # Hidden venues (reported closed and approved) are kept in the DB so the flag
    # survives OSM re-imports, but they are excluded from the exported map.
    # branded_only skips venues without a single brand edge — since the country
    # sweep the brandless majority (~250k rows) is served per-viewport by
    # /api/gray instead of being exported.
    where = "COALESCE(hidden, 0) = 0" + (f" AND {_HAS_EDGE}" if branded_only else "")
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website, opening_hours FROM venues "
        f"WHERE {where} ORDER BY id"
    ).fetchall()
    return [_venue_dict(conn, v) for v in venues]


def fetch_gray_in_bbox(conn, south: float, west: float, north: float, east: float) -> list[dict]:
    """Brandless, visible venues inside the bbox — the /api/gray tile payload."""
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website, opening_hours FROM venues "
        "WHERE lon >= ? AND lon < ? AND lat >= ? AND lat < ? "
        f"AND COALESCE(hidden, 0) = 0 AND NOT {_HAS_EDGE} ORDER BY id",
        (west, east, south, north),
    ).fetchall()
    return [_venue_dict(conn, v, with_brands=False) for v in venues]


def search_venues_db(conn, query: str, limit: int = 30) -> list[dict]:
    """Nationwide name/address search over the folded `search_key` column.

    Every folded query token must appear as a substring; ranking beyond
    "name-prefix matches first, shorter names first" is left to the client,
    which rescores merged results with its own field-weighted scorer."""
    tokens = fold(query).split()
    if not tokens:
        return []
    where = " AND ".join("search_key LIKE ?" for _ in tokens)
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website, opening_hours FROM venues "
        f"WHERE COALESCE(hidden, 0) = 0 AND search_key IS NOT NULL AND {where} "
        "ORDER BY (search_key LIKE ?) DESC, length(name), id LIMIT ?",
        [f"%{t}%" for t in tokens] + [f"{tokens[0]}%", limit],
    ).fetchall()
    return [_venue_dict(conn, v) for v in venues]


_SUB_COLS = ("kind", "venue_osm_id", "venue_name", "lat", "lon",
             "brand", "serving", "beer", "address", "opening_hours", "note",
             "submitter_ip")


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
