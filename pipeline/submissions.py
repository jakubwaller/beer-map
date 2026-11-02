from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from . import config, dedupe
from .curation import slugify
from .db import (
    count_submissions_since, delete_edges, get_submission, list_submissions,
    rename_venue, set_submission_status, set_venue_hidden, update_venue_address,
    update_venue_hours, upsert_brand, upsert_edge, upsert_venue,
)
from .export import export_geojson
from .geocode import geocode_address
from .matching import haversine_m
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
# Literal spaces and re.ASCII: \s would let a newline through and \d a non-ASCII digit.
_RULE = rf"(?:{_SEL} +(?:off|closed|{_RANGE}(?:,{_RANGE})*))"
_HOURS_RE = re.compile(rf"(?:24/7|{_RULE}(?: *; *{_RULE})*)", re.ASCII)
_RANGE_RE = re.compile(_RANGE, re.ASCII)
_BRAND_KINDS = ("add", "remove")
_VENUE_KINDS = ("edit_venue", "close_venue", "edit_hours")
_NEW_VENUE_KIND = "add_venue"
KINDS = _BRAND_KINDS + _VENUE_KINDS + (_NEW_VENUE_KIND,)
# No edit_hours: osm_push uploads those to OSM as visitor reports on this site.
PARTNER_KINDS = ("add", "remove", "edit_venue", "close_venue", "add_venue")
_OSM_ID_RE = re.compile(r"(?:node|way|relation)/[1-9]\d*", re.ASCII)


def validate_submission(payload: dict, partner: bool = False) -> str | None:
    """The rules for a public submission. `partner=True` (the partner API,
    docs/PARTNER-API.md) also lets `add` say serving 'unknown', `edit_venue`
    carry a new `name` instead of an address, and `add_venue` come with
    coordinates instead of an address (validate_partner_submission checks those)."""
    kind = payload.get("kind")
    if kind not in KINDS:
        return "kind must be one of " + ", ".join(KINDS)
    if kind != _NEW_VENUE_KIND and not payload.get("venue_osm_id"):
        return "venue_osm_id required"
    if payload.get("note") and len(payload["note"]) > 300:
        return "note too long"
    servings = _SERVINGS | {"unknown"} if partner else _SERVINGS
    if kind in _BRAND_KINDS:
        brand = (payload.get("brand") or "").strip()
        if not brand or len(brand) > 80:
            return "brand must be 1-80 chars"
        if payload.get("beer") and len(payload["beer"]) > 80:
            return "beer must be at most 80 chars"
        if kind == "add" and payload.get("serving") not in servings:
            return "serving must be 'fass' or 'tank'" + (" or 'unknown'" if partner else "")
    elif kind == "edit_venue":
        address = (payload.get("address") or "").strip()
        new_name = (payload.get("name") or "").strip() if partner else ""
        if len(new_name) > 120:
            return "name must be at most 120 chars"
        if (address or not new_name) and not 0 < len(address) <= 200:
            return "address must be 1-200 chars" + (" (or send a new name)" if partner else "")
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
        if (address or not partner) and not 0 < len(address) <= 200:
            return "address must be 1-200 chars"
        brand = (payload.get("brand") or "").strip()
        if brand and len(brand) > 80:
            return "brand must be at most 80 chars"
        if brand and payload.get("serving") not in servings:
            return "serving must be 'fass' or 'tank'" + (" or 'unknown'" if partner else "")
    return None


def validate_partner_submission(payload: dict, venue_known: bool) -> str | None:
    """A partner API body (`venue` still nested): its own fields, then the
    public rules. `venue_known` = venue_osm_id is already in `venues`."""
    kind = payload.get("kind")
    if kind not in PARTNER_KINDS:
        return "kind must be one of " + ", ".join(PARTNER_KINDS)
    if not 0 < len(payload.get("ref") or "") <= 64:
        return "ref must be 1-64 chars"
    hint = payload.get("venue")
    if hint is not None:
        south, west, north, east = config.COUNTRY_BBOX  # also refuses NaN
        if not (south <= hint["lat"] <= north and west <= hint["lon"] <= east):
            return "venue lat/lon outside the map (DE, AT, CZ)"
    if kind == _NEW_VENUE_KIND:
        if hint is None:
            return "venue {lat, lon} required for add_venue"
    elif not venue_known:
        if not _OSM_ID_RE.fullmatch(payload.get("venue_osm_id") or ""):
            return "venue_osm_id must look like node/123, way/123 or relation/123"
        if hint is None:
            return "unknown venue: send venue {name, lat, lon} to create it"
        name = (hint.get("name") or "").strip()
        if not name or len(name) > 120:
            return "venue.name must be 1-120 chars"
    return validate_submission(payload, partner=True)


def partner_ref_exists(conn, partner: str, ref: str) -> bool:
    return conn.execute("SELECT 1 FROM submissions WHERE partner=? AND ref=?",
                        (partner, ref)).fetchone() is not None


def within_partner_cap(conn, partner: str, now: datetime) -> bool:
    since = (now - timedelta(hours=24)).isoformat()
    n = conn.execute("SELECT COUNT(*) FROM submissions WHERE partner=? AND created_at >= ?",
                     (partner, since)).fetchone()[0]
    return n < config.PARTNER_DAILY_CAP


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


def _community_osm_id(conn, sub: dict, lat: float, lon: float) -> str:
    """`community/<slug>`, or `community/<slug>-<id>` when the slug is empty or
    already belongs to a different place (other name, or more than 200 m away)."""
    slug = slugify(sub["venue_name"])
    osm_id = "community/" + slug
    row = conn.execute("SELECT name, lat, lon FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    taken = row and (row["name"] != sub["venue_name"]
                     or haversine_m(row["lat"], row["lon"], lat, lon) > 200)
    if slug and not taken:
        return osm_id
    return f"{osm_id}-{sub.get('id')}"


def _apply_add_venue(conn, sub: dict, today: str) -> bool:
    """Create the submitted venue, geocoding its address on first apply.

    The hit is written back to the submission row so nightly re-applies (and
    re-applies after the venue was pruned) reuse the stored coordinates instead
    of asking Nominatim again. No coordinates and no geocode hit means the
    venue cannot be placed — the submission stays pending for the moderator.
    A venue that already exists (linked by the moderator, or created by an
    earlier apply) is left as it is; only the brand is attached.
    """
    osm_id = sub.get("assigned_osm_id")
    vid = _venue_id(conn, osm_id) if osm_id else None
    if vid is None:
        vid = _create_add_venue(conn, sub, osm_id, today)
        if vid is None:
            return False
    brand = (sub.get("brand") or "").strip()
    if brand:
        bid = upsert_brand(conn, config.normalize_brand(brand))
        upsert_edge(conn, vid, bid, "community", today,
                    serving=sub.get("serving", "unknown"), beer=sub.get("beer") or None)
    return True


def _create_add_venue(conn, sub: dict, osm_id: str | None, today: str) -> int | None:
    lat, lon = sub.get("lat"), sub.get("lon")
    if lat is None or lon is None:
        coords = geocode_address(sub["address"])
        if coords is None:
            return None
        lat, lon = coords
        if sub.get("id") is not None:
            conn.execute("UPDATE submissions SET lat=?, lon=? WHERE id=?",
                         (lat, lon, sub["id"]))
    osm_id = osm_id or _community_osm_id(conn, sub, lat, lon)
    if sub.get("id") is not None:
        conn.execute("UPDATE submissions SET assigned_osm_id=? WHERE id=?", (osm_id, sub["id"]))
    return upsert_venue(conn, Venue(osm_id, sub["venue_name"], lat, lon,
                                    sub.get("address")), today)


def apply_one(conn, sub: dict, today: str) -> bool:
    kind = sub.get("kind")
    if kind == _NEW_VENUE_KIND:
        return _apply_add_venue(conn, sub, today)
    osm_id = sub.get("venue_osm_id")
    if _venue_id(conn, osm_id) is None:
        if sub.get("hint_lat") is None:
            return False
        # A partner report on a venue the sweeps don't have: the venue comes
        # from its hint, here and on every nightly re-apply should it be lost.
        upsert_venue(conn, Venue(osm_id, sub["venue_name"], sub["hint_lat"],
                                 sub["hint_lon"]), today)
    # Venue-level edits run after OSM re-imports each build (see run_pipeline), so
    # an approved address change or "closed" flag keeps overriding the OSM data.
    if kind == "edit_venue":
        if sub.get("new_name"):  # partner renames; re-applied after every OSM import
            rename_venue(conn, osm_id, sub["new_name"])
        if not sub.get("address"):
            return True
        # Move the pin too — a text-only address edit would leave the map marker
        # at the old (OSM-imported) coordinates. The search is bounded near the
        # current pin so the venue stays in its city. Geocoding failure isn't
        # fatal: fall back to updating just the address text.
        row = conn.execute(
            "SELECT lat, lon FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
        lat, lon = sub.get("lat"), sub.get("lon")
        if lat is None or lon is None:
            coords = geocode_address(sub["address"], near=(row["lat"], row["lon"]))
            lat, lon = coords if coords else (None, None)
            if coords and sub.get("id") is not None:  # stored so replays skip Nominatim
                conn.execute("UPDATE submissions SET lat=?, lon=? WHERE id=?",
                             (lat, lon, sub["id"]))
        update_venue_address(conn, osm_id, sub["address"], lat, lon)
        return True
    if kind == "edit_hours":
        if (sub.get("osm_changeset") or 0) > 0:  # uploaded: OSM owns the hours now
            return True
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
        if possible_duplicate(conn, sub):
            continue  # a new venue next to an existing one wants a moderator's look
        if not apply_one(conn, sub, today):
            continue  # stays pending, same as single approve
        set_submission_status(conn, sub["id"], "approved", today)
        approved += 1
    conn.commit()
    if approved:  # one export for the whole batch, not one per submission
        export_geojson(conn, out_path)
    return approved


def possible_duplicate(conn, sub: dict) -> bool:
    """An unlinked add_venue with a look-alike venue already in our DB."""
    return (sub["kind"] == _NEW_VENUE_KIND and not sub.get("assigned_osm_id")
            and dedupe.origin(sub) is not None
            and bool(dedupe.db_candidates(conn, sub["venue_name"], *dedupe.origin(sub))))


def link_submission(conn, sub_id: int, osm_id: str) -> bool:
    """Point a pending add_venue at an existing venue or OSM element (one of
    its duplicate candidates), so approval attaches to it instead of creating
    a community venue. Its coordinates become the submission's, so the
    curation export can recreate it; the reported point is kept in
    hint_lat/hint_lon. An empty `osm_id` undoes the link and puts it back."""
    sub = get_submission(conn, sub_id)
    if not sub or sub["status"] != "pending" or sub["kind"] != _NEW_VENUE_KIND:
        return False
    if not osm_id:
        conn.execute("UPDATE submissions SET assigned_osm_id=NULL, lat=COALESCE(hint_lat, lat), "
                     "lon=COALESCE(hint_lon, lon), hint_lat=NULL, hint_lon=NULL WHERE id=?",
                     (sub_id,))
        conn.commit()
        return True
    cands, _ = dedupe.candidates(conn, sub)
    point = dedupe.origin(sub)
    hit = next((c for c in cands if c["osm_id"] == osm_id), None)
    if hit is None:
        return False
    conn.execute("UPDATE submissions SET assigned_osm_id=?, lat=?, lon=?, hint_lat=?, hint_lon=? "
                 "WHERE id=?", (osm_id, hit["lat"], hit["lon"], *point, sub_id))
    conn.commit()
    return True


def reject_submission(conn, sub_id: int, today: str) -> bool:
    sub = get_submission(conn, sub_id)
    if not sub or sub["status"] != "pending":
        return False
    set_submission_status(conn, sub_id, "rejected", today)
    conn.commit()
    return True
