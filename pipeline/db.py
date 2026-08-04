from __future__ import annotations

import re
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
    ("venues", "opening_hours", "TEXT"),
    ("venue_brand", "beer", "TEXT"),
    ("submissions", "address", "TEXT"),
    ("submissions", "beer", "TEXT"),
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
    scrub_plaintext_ips(conn)
    conn.commit()


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
        INSERT INTO venues (osm_id, name, lat, lon, address, website, opening_hours, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(osm_id) DO UPDATE SET
            name=excluded.name, lat=excluded.lat, lon=excluded.lon,
            address=excluded.address, website=excluded.website,
            opening_hours=excluded.opening_hours, updated_at=excluded.updated_at
        """,
        (venue.osm_id, venue.name, venue.lat, venue.lon, venue.address, venue.website,
         venue.opening_hours, seen),
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
    if lat is not None and lon is not None:
        cur = conn.execute(
            "UPDATE venues SET address=?, lat=?, lon=? WHERE osm_id=?",
            (address, lat, lon, osm_id))
    else:
        cur = conn.execute("UPDATE venues SET address=? WHERE osm_id=?", (address, osm_id))
    return cur.rowcount


def set_venue_hidden(conn, osm_id: str, hidden: bool) -> int:
    cur = conn.execute("UPDATE venues SET hidden=? WHERE osm_id=?", (1 if hidden else 0, osm_id))
    return cur.rowcount


def fetch_venues_with_brands(conn) -> list[dict]:
    # Hidden venues (reported closed and approved) are kept in the DB so the flag
    # survives OSM re-imports, but they are excluded from the exported map.
    venues = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, website, opening_hours FROM venues "
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
            "opening_hours": v["opening_hours"], "brands": brands,
        })
    return out


_SUB_COLS = ("kind", "venue_osm_id", "venue_name", "lat", "lon",
             "brand", "serving", "beer", "address", "note", "submitter_ip")


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
