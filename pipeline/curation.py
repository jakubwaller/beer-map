from __future__ import annotations

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
