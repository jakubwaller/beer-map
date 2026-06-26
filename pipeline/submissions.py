from __future__ import annotations

from datetime import datetime, timedelta

from . import config
from .db import (
    count_submissions_since, delete_edges, get_submission, list_submissions,
    set_submission_status, set_venue_hidden, update_venue_address, upsert_brand,
    upsert_edge,
)
from .export import export_geojson

_SERVINGS = {"fass", "tank"}
_BRAND_KINDS = ("add", "remove")
_VENUE_KINDS = ("edit_venue", "close_venue")
KINDS = _BRAND_KINDS + _VENUE_KINDS


def validate_submission(payload: dict) -> str | None:
    kind = payload.get("kind")
    if kind not in KINDS:
        return "kind must be one of " + ", ".join(KINDS)
    if not payload.get("venue_osm_id"):
        return "venue_osm_id required"
    if payload.get("note") and len(payload["note"]) > 300:
        return "note too long"
    if kind in _BRAND_KINDS:
        brand = (payload.get("brand") or "").strip()
        if not brand or len(brand) > 80:
            return "brand must be 1-80 chars"
        if kind == "add" and payload.get("serving") not in _SERVINGS:
            return "serving must be 'fass' or 'tank'"
    elif kind == "edit_venue":
        address = (payload.get("address") or "").strip()
        if not address or len(address) > 200:
            return "address must be 1-200 chars"
    return None


def within_rate_limit(conn, ip: str, now: datetime) -> bool:
    since = (now - timedelta(seconds=config.RATE_WINDOW_S)).isoformat()
    return count_submissions_since(conn, ip, since) < config.RATE_LIMIT


def _venue_id(conn, osm_id):
    row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    return row["id"] if row else None


def apply_one(conn, sub: dict, today: str) -> bool:
    osm_id = sub.get("venue_osm_id")
    if _venue_id(conn, osm_id) is None:
        return False
    kind = sub.get("kind")
    # Venue-level edits run after OSM re-imports each build (see run_pipeline), so
    # an approved address change or "closed" flag keeps overriding the OSM data.
    if kind == "edit_venue":
        update_venue_address(conn, osm_id, sub["address"])
        return True
    if kind == "close_venue":
        set_venue_hidden(conn, osm_id, True)
        return True
    vid = _venue_id(conn, osm_id)
    bid = upsert_brand(conn, config.normalize_brand(sub["brand"]))
    if kind == "remove":
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
