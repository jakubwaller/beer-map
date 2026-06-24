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
