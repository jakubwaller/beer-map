from __future__ import annotations

import re

import yaml
from rapidfuzz import fuzz

from .config import normalize_brand
from .db import delete_edges, list_submissions, upsert_brand, upsert_edge, upsert_venue
from .models import Venue


def load_curation(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or []
    except FileNotFoundError:
        return []


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _resolve_venue_id(conn, entry, venues, today):
    if entry.get("osm_id"):
        row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (entry["osm_id"],)).fetchone()
        if row:
            return row["id"]
        # An osm_id plus coordinates recreates the venue (e.g. re-importing
        # exported community add_venue entries into a fresh database).
        if entry.get("lat") is not None and entry.get("lon") is not None:
            return upsert_venue(conn, Venue(entry["osm_id"], entry["venue"],
                                            entry["lat"], entry["lon"],
                                            entry.get("address"), entry.get("website")), today)
        return None
    if entry.get("lat") is not None and entry.get("lon") is not None:
        osm_id = "manual/" + slugify(entry["venue"])
        return upsert_venue(conn, Venue(osm_id, entry["venue"], entry["lat"], entry["lon"],
                                        entry.get("address"), entry.get("website")), today)
    best, best_score = None, -1.0
    for v in venues:
        score = fuzz.token_sort_ratio(entry["venue"].lower(), v.name.lower())
        if score >= 85 and score > best_score:
            best, best_score = v, score
    if best is None:
        return None
    row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (best.osm_id,)).fetchone()
    return row["id"] if row else None


def approved_community_entries(conn) -> list[dict]:
    """Approved community submissions rendered as curation.yaml entries.

    This lets verified community contributions be committed to git (IP-free,
    human-readable) so they survive a database loss — the live DB is the only
    place they otherwise exist. Brand add/remove entries resolve by exact
    `osm_id`; add_venue entries carry the geocoded coordinates so re-applying
    them recreates the venue. Venue address edits and closures have no curation
    equivalent yet and are skipped.
    """
    entries = []
    for s in list_submissions(conn, "approved"):
        verified = (s["decided_at"] or s["created_at"] or "")[:10]
        if s["kind"] == "add_venue":
            if s["lat"] is None or s["lon"] is None:
                continue  # approved but never applied — nothing to pin
            entry = {"osm_id": "community/" + slugify(s["venue_name"]),
                     "venue": s["venue_name"], "lat": s["lat"], "lon": s["lon"]}
            if s["address"]:
                entry["address"] = s["address"]
            if s["brand"]:
                entry["brand"] = s["brand"]
                entry["serving"] = s["serving"]
        elif s["kind"] in ("add", "remove"):
            entry = {"osm_id": s["venue_osm_id"], "brand": s["brand"]}
            if s["kind"] == "add":
                entry["serving"] = s["serving"]
            entry["action"] = s["kind"]
        else:
            continue
        entry["verified"] = verified
        entry["note"] = f"community-approved ({s['venue_name']})"
        entries.append(entry)
    return entries


def apply_curation(conn, entries, venues, today) -> dict:
    counts = {"added": 0, "removed": 0, "skipped": 0}
    for entry in entries:
        vid = _resolve_venue_id(conn, entry, venues, today)
        if vid is None:
            counts["skipped"] += 1
            continue
        if not entry.get("brand"):
            # Venue-only entry: pins a place the OSM sweep misses (e.g. tagged
            # shop=alcohol); its beers come from community reports later.
            counts["added"] += 1
            continue
        bid = upsert_brand(conn, normalize_brand(entry["brand"]))
        if entry.get("action", "add") == "remove":
            delete_edges(conn, vid, bid)
            counts["removed"] += 1
        else:
            upsert_edge(conn, vid, bid, "manual", entry.get("verified") or today,
                        serving=entry.get("serving", "unknown"), beer=entry.get("beer"))
            counts["added"] += 1
    return counts
