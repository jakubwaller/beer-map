from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from . import config
from .curation import slugify
from .db import (
    count_submissions_since, delete_edges, get_submission, list_submissions,
    set_submission_status, set_venue_hidden, update_venue_address,
    update_venue_hours, upsert_brand, upsert_edge, upsert_venue,
)
from .export import export_geojson
from .geocode import geocode_address
from .models import Venue

_SERVINGS = {"fass", "tank"}
# One rule is a day selector (Mo, Mo-Fr, "Mo,We", optionally empty) followed by
# either `off`/`closed` or up to a few HH:MM-HH:MM ranges; rules join with `;`.
# Deliberately narrower than real opening_hours: anything web/hours.js cannot
# read would show up as an uninterpreted string on the map, which is the state
# this feature exists to fix.
_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_SEL = rf"(?:{_DAY}(?:-{_DAY})?)(?:,{_DAY}(?:-{_DAY})?)*"
# 24:00 is the end of the day and the only hour-24 time there is: web/hours.js
# refuses anything past 1440, so 24:30 would validate here and then sit on the
# venue as a string the map cannot read.
_RANGE = r"(?:[01]\d|2[0-3]):[0-5]\d-(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)"
_RULE = rf"(?:{_SEL}\s+(?:off|closed|{_RANGE}(?:,{_RANGE})*))"
_HOURS_RE = re.compile(rf"(?:24/7|{_RULE}(?:\s*;\s*{_RULE})*)")
_RANGE_RE = re.compile(_RANGE)
_BRAND_KINDS = ("add", "remove")
_VENUE_KINDS = ("edit_venue", "close_venue", "edit_hours")
_NEW_VENUE_KIND = "add_venue"
KINDS = _BRAND_KINDS + _VENUE_KINDS + (_NEW_VENUE_KIND,)


def validate_submission(payload: dict) -> str | None:
    kind = payload.get("kind")
    if kind not in KINDS:
        return "kind must be one of " + ", ".join(KINDS)
    if kind != _NEW_VENUE_KIND and not payload.get("venue_osm_id"):
        return "venue_osm_id required"
    if payload.get("note") and len(payload["note"]) > 300:
        return "note too long"
    if kind in _BRAND_KINDS:
        brand = (payload.get("brand") or "").strip()
        if not brand or len(brand) > 80:
            return "brand must be 1-80 chars"
        if payload.get("beer") and len(payload["beer"]) > 80:
            return "beer must be at most 80 chars"
        if kind == "add" and payload.get("serving") not in _SERVINGS:
            return "serving must be 'fass' or 'tank'"
    elif kind == "edit_venue":
        address = (payload.get("address") or "").strip()
        if not address or len(address) > 200:
            return "address must be 1-200 chars"
    elif kind == "edit_hours":
        hours = (payload.get("opening_hours") or "").strip()
        if not hours or len(hours) > 200:
            return "opening_hours must be 1-200 chars"
        # The UI builds this from a weekday grid, but the endpoint is public, so
        # the shape is checked here too: day selectors, clock ranges, `off`, or
        # 24/7, separated by `;` — the subset web/hours.js can actually read.
        if not _HOURS_RE.fullmatch(hours):
            return "opening_hours must look like 'Mo-Fr 10:00-22:00; Sa off'"
        # Every day `off` is a closure report, not opening hours — the grid
        # refuses to build one, and applying it would leave the venue
        # permanently never-open through every re-import.
        if hours != "24/7" and not _RANGE_RE.search(hours):
            return "opening_hours must give at least one time range"
    elif kind == _NEW_VENUE_KIND:
        name = (payload.get("name") or "").strip()
        if not name or len(name) > 120:
            return "name must be 1-120 chars"
        address = (payload.get("address") or "").strip()
        if not address or len(address) > 200:
            return "address must be 1-200 chars"
        brand = (payload.get("brand") or "").strip()
        if brand and len(brand) > 80:
            return "brand must be at most 80 chars"
        if brand and payload.get("serving") not in _SERVINGS:
            return "serving must be 'fass' or 'tank'"
    return None


def hash_ip(ip: str) -> str:
    """Turn a client IP into the opaque key stored as `submitter_ip`.

    Rate limiting only needs "same client as before?", never the address
    itself, so the raw IP never reaches the database. The `h:` prefix marks
    hashed values so `db.scrub_plaintext_ips` can spot pre-hashing rows.
    """
    digest = hashlib.sha256(f"{config.IP_SALT}\x00{ip}".encode()).hexdigest()
    return "h:" + digest[:32]


def within_rate_limit(conn, ip: str, now: datetime) -> bool:
    since = (now - timedelta(seconds=config.RATE_WINDOW_S)).isoformat()
    return count_submissions_since(conn, ip, since) < config.RATE_LIMIT


def _venue_id(conn, osm_id):
    row = conn.execute("SELECT id FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    return row["id"] if row else None


def _apply_add_venue(conn, sub: dict, today: str) -> bool:
    """Create the submitted venue, geocoding its address on first apply.

    The hit is written back to the submission row so nightly re-applies (and
    re-applies after the venue was pruned) reuse the stored coordinates instead
    of asking Nominatim again. No coordinates and no geocode hit means the
    venue cannot be placed — the submission stays pending for the moderator.
    """
    lat, lon = sub.get("lat"), sub.get("lon")
    if lat is None or lon is None:
        coords = geocode_address(sub["address"])
        if coords is None:
            return False
        lat, lon = coords
        if sub.get("id") is not None:
            conn.execute("UPDATE submissions SET lat=?, lon=? WHERE id=?",
                         (lat, lon, sub["id"]))
    osm_id = "community/" + slugify(sub["venue_name"])
    vid = upsert_venue(conn, Venue(osm_id, sub["venue_name"], lat, lon,
                                   sub.get("address")), today)
    brand = (sub.get("brand") or "").strip()
    if brand:
        bid = upsert_brand(conn, config.normalize_brand(brand))
        upsert_edge(conn, vid, bid, "community", today,
                    serving=sub.get("serving", "unknown"), beer=sub.get("beer") or None)
    return True


def apply_one(conn, sub: dict, today: str) -> bool:
    kind = sub.get("kind")
    if kind == _NEW_VENUE_KIND:
        return _apply_add_venue(conn, sub, today)
    osm_id = sub.get("venue_osm_id")
    if _venue_id(conn, osm_id) is None:
        return False
    # Venue-level edits run after OSM re-imports each build (see run_pipeline), so
    # an approved address change or "closed" flag keeps overriding the OSM data.
    if kind == "edit_venue":
        # Move the pin too — a text-only address edit would leave the map marker
        # at the old (OSM-imported) coordinates. The search is bounded near the
        # current pin so the venue stays in its city. Geocoding failure isn't
        # fatal: fall back to updating just the address text.
        row = conn.execute(
            "SELECT lat, lon FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
        coords = geocode_address(sub["address"], near=(row["lat"], row["lon"]))
        lat, lon = coords if coords else (None, None)
        update_venue_address(conn, osm_id, sub["address"], lat, lon)
        return True
    if kind == "edit_hours":
        update_venue_hours(conn, osm_id, sub["opening_hours"])
        return True
    if kind == "close_venue":
        set_venue_hidden(conn, osm_id, True)
        return True
    vid = _venue_id(conn, osm_id)
    bid = upsert_brand(conn, config.normalize_brand(sub["brand"]))
    beer = sub.get("beer") or None
    if kind == "remove":
        # A specific beer removes just that beer; no beer removes the whole brand.
        delete_edges(conn, vid, bid, beer=beer)
    else:
        upsert_edge(conn, vid, bid, "community", today,
                    serving=sub.get("serving", "unknown"), beer=beer)
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
    # A submission that cannot be applied (venue gone, add_venue address that
    # won't geocode) stays pending instead of being approved into a no-op.
    if not apply_one(conn, sub, today):
        return False
    set_submission_status(conn, sub_id, "approved", today)
    conn.commit()
    export_geojson(conn, out_path)
    return True


def approve_all_pending(conn, today: str, out_path: str) -> int:
    approved = 0
    for sub in list_submissions(conn, "pending"):
        if not apply_one(conn, sub, today):
            continue  # stays pending, same as single approve
        set_submission_status(conn, sub["id"], "approved", today)
        approved += 1
    conn.commit()
    if approved:  # one export for the whole batch, not one per submission
        export_geojson(conn, out_path)
    return approved


def reject_submission(conn, sub_id: int, today: str) -> bool:
    sub = get_submission(conn, sub_id)
    if not sub or sub["status"] != "pending":
        return False
    set_submission_status(conn, sub_id, "rejected", today)
    conn.commit()
    return True
