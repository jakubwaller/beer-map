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
