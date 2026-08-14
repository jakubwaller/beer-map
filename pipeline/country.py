"""Nationwide venue sweep: every pub/bar/biergarten/restaurant/cafe in the
covered countries, fetched tile by tile.

One Overpass query for all of Germany is infeasible — ~250k elements; even a
count-only query exceeds the 300 s server timeout. So this walks a grid of
bbox tiles over COUNTRY_BBOX, clips each tile query to COUNTRY_CODES and
upserts the results into the same `venues` table the nightly pipeline
maintains (the nightly run only upserts, never deletes, so the two coexist).
A tile the server cannot answer (timeout, too much data) is quartered and its
quarters retried; successfully fetched tiles are recorded in `country_tiles`,
so `--resume` continues an interrupted run instead of starting over.

Venues gathered here are never exported to GeoJSON — they are the brandless
substrate the frontend fetches per viewport from /api/gray (and the reason a
village pub anywhere in DE/CZ/AT can now receive community submissions).

    python -m pipeline.country            # full sweep (~145 requests)
    python -m pipeline.country --resume   # continue an interrupted run
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta

from . import osm
from .config import _AMENITY, COUNTRY_BBOX, COUNTRY_CODES, DB_PATH, normalize_brand
from .db import get_connection, init_db, upsert_brand, upsert_edge, upsert_venue

# Tiles whose every span is at or below this give up instead of splitting
# further; 1.0° tiles quarter at most twice (1.0 -> 0.5 -> 0.25).
MIN_TILE_DEG = 0.3
RESUME_WINDOW_H = 24


def build_tile_ql(south, west, north, east,
                  country_codes=COUNTRY_CODES, timeout=120) -> str:
    countries = "|".join(country_codes)
    return (
        f'[out:json][timeout:{timeout}];'
        f'area["ISO3166-1"~"^({countries})$"]["admin_level"="2"]->.countries;'
        f'nwr[{_AMENITY}](area.countries)({south},{west},{north},{east});'
        'out center tags;'
    )


def grid(bbox=COUNTRY_BBOX, step=1.0) -> list[tuple[float, float, float, float]]:
    """(south, west, north, east) tiles covering the bbox, clamped to its edge.
    Coordinates are rounded so tile keys are stable across runs."""
    south, west, north, east = bbox
    tiles = []
    i = 0
    while (lat := round(south + i * step, 4)) < north:
        j = 0
        while (lon := round(west + j * step, 4)) < east:
            tiles.append((lat, lon, round(min(lat + step, north), 4),
                          round(min(lon + step, east), 4)))
            j += 1
        i += 1
    return tiles


def _key(s, w, n, e) -> str:
    return f"{s:.4g},{w:.4g},{n:.4g},{e:.4g}"


def _quarters(s, w, n, e):
    mid_lat, mid_lon = round((s + n) / 2, 4), round((w + e) / 2, 4)
    quads = ((s, w, mid_lat, mid_lon), (s, mid_lon, mid_lat, e),
             (mid_lat, w, n, mid_lon), (mid_lat, mid_lon, n, e))
    return [q for q in quads if q[2] > q[0] and q[3] > q[1]]


def sweep_country(db_path=DB_PATH, bbox=COUNTRY_BBOX, tile_deg=1.0, resume=False,
                  fetch=osm.fetch_overpass, sleep_s=1.0, today=None) -> dict:
    today = today or date.today().isoformat()
    conn = get_connection(db_path)
    init_db(conn)
    fresh: set[str] = set()
    if resume:
        cutoff = (datetime.now() - timedelta(hours=RESUME_WINDOW_H)).isoformat()
        fresh = {r["tile"] for r in conn.execute(
            "SELECT tile FROM country_tiles WHERE fetched_at >= ?", (cutoff,))}

    stack = list(reversed(grid(bbox, tile_deg)))
    stats = {"tiles": 0, "skipped": 0, "split": 0, "failed": 0,
             "venues": 0, "edges": 0}
    failures: list[str] = []
    while stack:
        s, w, n, e = stack.pop()
        key = _key(s, w, n, e)
        if key in fresh:
            stats["skipped"] += 1
            continue
        try:
            data = fetch(build_tile_ql(s, w, n, e))
            # An exceeded timeout during output can come back as HTTP 200 with
            # a partial element list and only a "remark" admitting it —
            # treating that as success would silently hole the coverage.
            remark = data.get("remark") or ""
            if "timed out" in remark or "error" in remark.lower():
                raise RuntimeError(f"overpass remark: {remark[:160]}")
        except Exception as exc:  # noqa: BLE001 — any failure: split or give up
            if n - s > MIN_TILE_DEG or e - w > MIN_TILE_DEG:
                stats["split"] += 1
                stack.extend(reversed(_quarters(s, w, n, e)))
                print(f"tile {key} failed ({exc}); splitting", file=sys.stderr)
            else:
                stats["failed"] += 1
                failures.append(key)
                print(f"WARN: tile {key} failed for good: {exc}", file=sys.stderr)
            continue
        venues, edges = osm.parse_overpass(data)
        id_by_osm = {v.osm_id: upsert_venue(conn, v, today) for v in venues}
        for ed in edges:
            bid = upsert_brand(conn, normalize_brand(ed.brand))
            upsert_edge(conn, id_by_osm[ed.venue_osm_id], bid, ed.source, today,
                        serving=ed.serving)
        conn.execute(
            "INSERT INTO country_tiles (tile, fetched_at, venues) VALUES (?, ?, ?) "
            "ON CONFLICT(tile) DO UPDATE SET "
            "fetched_at=excluded.fetched_at, venues=excluded.venues",
            (key, datetime.now().isoformat(timespec="seconds"), len(venues)))
        conn.commit()
        stats["tiles"] += 1
        stats["venues"] += len(venues)
        stats["edges"] += len(edges)
        done = stats["tiles"] + stats["skipped"] + stats["failed"]
        print(f"[{done}/{done + len(stack)}] {key}: {len(venues)} venues", flush=True)
        if sleep_s and stack:
            time.sleep(sleep_s)
    stats["failures"] = failures
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Sweep every venue in the covered countries into the DB, "
                    "one bbox tile at a time.")
    ap.add_argument("--resume", action="store_true",
                    help=f"skip tiles fetched within the last {RESUME_WINDOW_H} h")
    ap.add_argument("--tile-deg", type=float, default=1.0,
                    help="edge length of the starting tiles (default 1.0)")
    ap.add_argument("--bbox", metavar="S,W,N,E",
                    help="sweep only this bbox (default: all of DE+CZ+AT)")
    args = ap.parse_args()
    box = (tuple(float(p) for p in args.bbox.split(","))
           if args.bbox else COUNTRY_BBOX)
    print(sweep_country(bbox=box, tile_deg=args.tile_deg, resume=args.resume))
