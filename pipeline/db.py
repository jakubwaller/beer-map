from __future__ import annotations

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
    address TEXT,
    note TEXT,
    submitter_ip TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""

# Columns added after the initial schema shipped. Existing databases were created
# with CREATE TABLE IF NOT EXISTS, so they keep the old layout — add them by hand.
_MIGRATIONS = (
    ("venues", "hidden", "INTEGER NOT NULL DEFAULT 0"),
    ("venue_brand", "beer", "TEXT"),
    ("submissions", "address", "TEXT"),
)


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
    conn.commit()


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


def delete_edges(conn, venue_id, brand_id) -> int:
    cur = conn.execute("DELETE FROM venue_brand WHERE venue_id=? AND brand_id=?", (venue_id, brand_id))
    return cur.rowcount


def update_venue_address(conn, osm_id: str, address: str) -> int:
    cur = conn.execute("UPDATE venues SET address=? WHERE osm_id=?", (address, osm_id))
    return cur.rowcount


def set_venue_hidden(conn, osm_id: str, hidden: bool) -> int:
    cur = conn.execute("UPDATE venues SET hidden=? WHERE osm_id=?", (1 if hidden else 0, osm_id))
    return cur.rowcount


def fetch_venues_with_brands(conn) -> list[dict]:
    # Hidden venues (reported closed and approved) are kept in the DB so the flag
    # survives OSM re-imports, but they are excluded from the exported map.
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website FROM venues "
        "WHERE COALESCE(hidden, 0) = 0 ORDER BY id"
    ).fetchall()
    out = []
    for v in venues:
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
            (v["id"],),
        ).fetchall()
        brands = []
        for e in edges:
            d = dict(e)
            d["beer"] = d["beer"] or None  # '' (brand-only) -> null in the export
            brands.append(d)
        out.append({
            "osm_id": v["osm_id"], "name": v["name"], "lat": v["lat"], "lon": v["lon"],
            "address": v["address"], "website": v["website"],
            "brands": brands,
        })
    return out


_SUB_COLS = ("kind", "venue_osm_id", "venue_name", "lat", "lon",
             "brand", "serving", "address", "note", "submitter_ip")


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
