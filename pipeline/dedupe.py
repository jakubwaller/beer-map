"""Duplicate check for add_venue submissions.

A reported "missing" place often exists already: as a venue in our DB, or in
OSM under a tag the sweeps don't import (fast_food, stadium, marketplace...).
The moderator sees these candidates in /admin and can link the submission to
one (`assigned_osm_id`), so approval attaches to it instead of creating a
`community/<slug>` venue. Nothing is written to OSM.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta

import httpx

from . import config
from .db import fold
from .geocode import geocode_address
from .matching import haversine_m

NAME_RADIUS_M = 75   # a similar name this close is probably the same place
NEAR_RADIUS_M = 15   # this close it is, whatever the name says
GEOCODE_INTERVAL_S = 1.0  # Nominatim usage policy
# An unnamed or differently named OSM object counts only when it could be the
# place itself — not the bench or bicycle stand next to it.
_VENUE_AMENITIES = {"pub", "bar", "biergarten", "restaurant", "cafe", "fast_food",
                    "food_court", "nightclub", "ice_cream"}
_KIND_KEYS = ("amenity", "shop", "leisure", "tourism", "craft", "building")


def similar(a: str | None, b: str | None) -> bool:
    """Equal after folding, or one contains the other as whole words (≥4 chars)."""
    a, b = fold(a), fold(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return short == long_ or (len(short) >= 4 and f" {short} " in f" {long_} ")


def _matches(name, cand_name, dist, venue_like) -> bool:
    return (dist <= NAME_RADIUS_M and similar(name, cand_name)) or \
        (dist <= NEAR_RADIUS_M and venue_like)


def _bbox(lat, lon, r):
    dlat = r / 111_320
    dlon = r / (111_320 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def db_candidates(conn, name, lat, lon) -> list[dict]:
    """Visible venues in our DB that look like the submitted place."""
    s, w, n, e = _bbox(lat, lon, NAME_RADIUS_M)
    rows = conn.execute(
        "SELECT osm_id, name, lat, lon FROM venues WHERE lat BETWEEN ? AND ? "
        "AND lon BETWEEN ? AND ? AND COALESCE(hidden, 0)=0", (s, n, w, e)).fetchall()
    out = []
    for r in rows:
        dist = haversine_m(lat, lon, r["lat"], r["lon"])
        if _matches(name, r["name"], dist, True):  # every DB venue is a venue
            out.append({"osm_id": r["osm_id"], "name": r["name"], "kind": "",
                        "lat": r["lat"], "lon": r["lon"], "dist": round(dist)})
    return sorted(out, key=lambda c: c["dist"])


def _get_map(south, west, north, east) -> dict:
    """The OSM API `map` call: every element in the bbox, ways with their nodes.
    Read-only, no slot queue like Overpass, and the bbox is ~150 m across."""
    resp = httpx.get(f"{config.OSM_API_URL.rstrip('/')}/api/0.6/map.json",
                     params={"bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"},
                     headers={"User-Agent": config.USER_AGENT}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def osm_candidates(name, lat, lon) -> list[dict]:
    """OSM elements (any tags) that look like the submitted place. Raises
    httpx.HTTPError / ValueError when OSM can't be asked."""
    elements = _get_map(*_bbox(lat, lon, NAME_RADIUS_M)).get("elements") or []
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in elements
             if e.get("type") == "node" and "lat" in e}
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        if el.get("type") == "node":
            pts = [nodes.get(el["id"])] if el["id"] in nodes else []
        elif el.get("type") == "way":
            pts = [nodes[i] for i in el.get("nodes", []) if i in nodes]
        else:
            # shortcut: relations come without geometry here, so a multipolygon
            # venue is missed; resolve their member ways if one ever matters.
            continue
        if not pts:
            continue
        dist = min(haversine_m(lat, lon, a, b) for a, b in pts)
        if _matches(name, tags.get("name"), dist, tags.get("amenity") in _VENUE_AMENITIES):
            kind = next((f"{k}={tags[k]}" for k in _KIND_KEYS if k in tags), "")
            out.append({"osm_id": f"{el['type']}/{el['id']}", "name": tags.get("name") or "",
                        "kind": kind,
                        "lat": round(sum(p[0] for p in pts) / len(pts), 7),
                        "lon": round(sum(p[1] for p in pts) / len(pts), 7),
                        "dist": round(dist)})
    return sorted(out, key=lambda c: c["dist"])


class Budget:
    """The network lookups (geocode, OSM) one /admin render may spend: at most
    `calls` of them, none after `seconds`, and no OSM after an OSM failure."""

    def __init__(self, calls: int = 3, seconds: float = 20.0):
        self.calls, self.until, self.osm_down = calls, time.monotonic() + seconds, False

    def take(self) -> bool:
        if self.calls <= 0 or time.monotonic() >= self.until:
            return False
        self.calls -= 1
        return True


_last_geocode = 0.0


def _geocode(address: str):
    """geocode_address at Nominatim's 1 request per second."""
    global _last_geocode
    wait = _last_geocode + GEOCODE_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return geocode_address(address)
    finally:
        _last_geocode = time.monotonic()


def origin(sub: dict):
    """Where the reporter put the place. Linking moves lat/lon to the linked
    candidate and parks the reported point in hint_lat/hint_lon."""
    if sub.get("hint_lat") is not None:
        return sub["hint_lat"], sub["hint_lon"]
    if sub.get("lat") is not None:
        return sub["lat"], sub["lon"]
    return None


def candidates(conn, sub: dict, budget: Budget | None = None) -> tuple[list[dict], bool | None]:
    """DB and OSM candidates for a pending add_venue, DB first, each tagged
    with `source` ("db" | "osm"). The second value is True when OSM answered
    (now or cached), False when it failed, None when it was not asked —
    no `budget` means no network at all.

    A visitor's submission has only an address: it is geocoded here and the
    hit stored on the row, exactly as approval would; a miss is stored too
    (`geocode_miss`) and not retried for a day. OSM answers are cached on
    the row, so reloading /admin doesn't ask again."""
    point = origin(sub)
    if point is None:
        miss = sub.get("geocode_miss") or ""
        retry = miss < (datetime.now() - timedelta(days=1)).isoformat()
        if not (sub.get("address") and retry and budget and budget.take()):
            return [], None
        point = _geocode(sub["address"])
        if point is None:
            sub["geocode_miss"] = datetime.now().isoformat()
            conn.execute("UPDATE submissions SET geocode_miss=? WHERE id=?",
                         (sub["geocode_miss"], sub["id"]))
            conn.commit()
            return [], None
        sub["lat"], sub["lon"] = point
        conn.execute("UPDATE submissions SET lat=?, lon=? WHERE id=?", (*point, sub["id"]))
        conn.commit()
    lat, lon = point
    found = [dict(c, source="db") for c in db_candidates(conn, sub["venue_name"], lat, lon)]
    osm, ok = None, None
    if sub.get("osm_candidates") is not None:
        osm, ok = json.loads(sub["osm_candidates"]), True
    elif budget and not budget.osm_down and budget.take():
        try:
            osm, ok = osm_candidates(sub["venue_name"], lat, lon), True
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            ok, budget.osm_down = False, True  # moderation goes on without OSM
        else:
            sub["osm_candidates"] = json.dumps(osm)
            conn.execute("UPDATE submissions SET osm_candidates=? WHERE id=?",
                         (sub["osm_candidates"], sub["id"]))
            conn.commit()
    known = {c["osm_id"] for c in found}
    found += [dict(c, source="osm") for c in osm or [] if c["osm_id"] not in known]
    return found, ok
