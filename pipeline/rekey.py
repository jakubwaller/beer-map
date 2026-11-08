"""Re-key venues whose OSM element is gone onto the element that survived.

A mapper who finds one place mapped twice deletes one element and keeps the
other; one who moves a pub from its building way onto a new node strips the
way of its amenity and name. Venues are keyed by osm_id and every import only
upserts, so the venue on the dead id lived on with everything attached to it
(brand edges, community reports, curation entries) while the survivor came in
beside it as a second, brandless venue. braena, Heußweg 93, Hamburg: node
10860057871 was deleted on 2026-09-18 next to node 3710412025 (same name, 1 m
away), and its Pilsner Urquell stayed on the dead id, where Dopivatel, which
matches taps by osm_id, cannot see it.

Candidate: a visible OSM venue no import has seen for GONE_AFTER_DAYS.
Survivor: a visible OSM venue an import did see in that window, with the same
fold()ed name, within RADIUS_M (GENERIC_RADIUS_M for a name many venues share),
and no other street address when both have one. With exactly one survivor the
OSM API is asked about the candidate; only an element that is deleted (410) or has no
name tag any more is merged into the survivor. None or several
survivors, or an element still there: left alone. The sweeps import every named
pub/bar/biergarten/restaurant/cafe, so a survivor is already a venue row and a
re-key is always a merge. Hidden (closed) venues take no part: they are off the
map, and carrying a closure over would hide the survivor.

Runs in every nightly build (pipeline/run.py) before the import writes, so the
lookups never hold the DB's write lock, and before curation and the community
re-apply, so both land on the survivor the same night. `python -m
pipeline.rekey` prints what the next build will do (lookups included) and
writes nothing.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta

import httpx

from .config import DB_PATH
from .db import fold, get_connection, init_db
from .dedupe import _bbox
from .matching import haversine_m
from .osm_push import OsmApi, parse_osm_id, tag_value

# The weekly country sweep (Sunday 05:00) refreshes every venue in DE/CZ/AT, so
# a live one is at most 7 days old at the 04:00 build before it; 10 leaves room
# for a sweep that ends late. Age only nominates, it proves nothing: partner-hint
# and /admin-linked venues are never refreshed by an import, and a failed sweep
# ages every country-only venue while a brewery-tagged neighbour stays fresh
# from the nightly. The OSM API has the last word (_gone_from_osm).
GONE_AFTER_DAYS = 10
# On the 2026-11-08 data every branded gone venue with a same-name survivor had
# it within 39 m, most within 5 m (a node dropped onto the building way it
# replaced); 150 m found no more. Ways and relations sit at their centre, as the
# import stores them, so a node and a way of one place compare like two nodes.
RADIUS_M = 50
# A name that this many OTHER visible venues (not the pair itself) carry within
# NAMESAKE_KM ("Kiosk", "Občerstvení", "Zur Post") says little: two of them 40 m
# apart can be two places, so they must be this close. venues has no country
# column and Nominatim is a network call, so "same country" is approximated by a
# distance that covers a country's cities but not a far-off namesake.
GENERIC_MIN = 2
GENERIC_RADIUS_M = 10
NAMESAKE_KM = 300
# OSM API lookups per night, branded venues first; the first run has a backlog,
# later nights only what was deleted since (and a recheck every GONE_AFTER_DAYS
# of what OSM said is still there).
MAX_LOOKUPS = 100
LOOKUP_DELAY_S = 1.0

_LIVE = ("(osm_id LIKE 'node/%' OR osm_id LIKE 'way/%' OR osm_id LIKE 'relation/%') "
         "AND COALESCE(hidden, 0) = 0")


def _same_street(a: str | None, b: str | None) -> bool:
    """The first address line ("Heußweg 93, 20255 Hamburg" -> "heussweg 93")
    is the same, or one of the two venues has none."""
    # shortcut: an addr:place address ("21a, 6215 Hinterriß") has no street line and
    # never matches one that has; compare housenumber + postcode if that blocks real merges.
    a, b = (fold((x or "").split(",")[0]) for x in (a, b))
    return not a or not b or a == b


def plan(conn, today: str):
    """(moves, ambiguous, unmatched) as of `today`: moves are (gone, survivor)
    venue rows, ambiguous are (gone, [survivors]), unmatched are gone rows.
    Nothing here asks OSM; rekey_gone does, for the moves."""
    cutoff = (date.fromisoformat(today) - timedelta(days=GONE_AFTER_DAYS)).isoformat()
    gone = conn.execute(
        "SELECT id, osm_id, name, lat, lon, address, EXISTS (SELECT 1 FROM venue_brand vb "
        "WHERE vb.venue_id = venues.id) AS branded "
        f"FROM venues WHERE {_LIVE} AND updated_at < ? ORDER BY id", (cutoff,)).fetchall()
    moves, ambiguous, unmatched = [], [], []
    names = None  # fold(name) -> visible venues carrying it, read once, only if needed
    for v in gone:
        key = fold(v["name"])  # '' (a name in another script) matches nothing
        s, w, n, e = _bbox(v["lat"], v["lon"], RADIUS_M)
        found = [(c, haversine_m(v["lat"], v["lon"], c["lat"], c["lon"])) for c in conn.execute(
            "SELECT id, osm_id, name, lat, lon, address FROM venues WHERE lon BETWEEN ? AND ? "
            f"AND lat BETWEEN ? AND ? AND {_LIVE} AND updated_at >= ? ORDER BY id",
            (w, e, s, n, cutoff)) if key and fold(c["name"]) == key]
        if found:
            if names is None:
                names = defaultdict(list)
                for r in conn.execute("SELECT id, name, lat, lon FROM venues "
                                      "WHERE COALESCE(hidden, 0) = 0"):
                    names[fold(r["name"])].append(r)
            found = [(c, dist, sum(
                o["id"] not in (v["id"], c["id"])
                and haversine_m(v["lat"], v["lon"], o["lat"], o["lon"]) <= NAMESAKE_KM * 1000
                for o in names[key]) >= GENERIC_MIN) for c, dist in found]
            found = [c for c, dist, generic in found
                     if dist <= (GENERIC_RADIUS_M if generic else RADIUS_M)
                     and _same_street(v["address"], c["address"])]
        if len(found) == 1:
            moves.append((v, found[0]))
        elif found:
            ambiguous.append((v, found))
        else:
            unmatched.append(v)
    return moves, ambiguous, unmatched


def _gone_from_osm(api, v) -> bool:
    """True when OSM has deleted the venue's element (410) or it has no name tag
    at all (a pub moved off its building way). Raises when OSM can't say; a 404
    (an id that never existed: wrong OSM_API_URL) is raised as such."""
    kind, oid = parse_osm_id(v["osm_id"])
    try:
        el = api.get_element(kind, oid)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 410:
            return True
        raise
    # Not compared with the stored name: a partner's name for the venue differs from OSM's.
    return el.get("visible") == "false" or not tag_value(el, "name")


def merge(conn, old, new, today: str) -> None:
    """Move venue `old` onto venue `new` (rows with id and osm_id), then drop it.

    Brand edges keep one row per brand, source and beer: the newest last_seen,
    the earliest first_seen, and the newer report's serving unless it is
    'unknown'. Submissions naming the old id are rewritten, so the nightly
    re-apply, the curation export and the OSM push all follow; earlier re-keys
    onto the old id move on too, and old -> new is kept for ids from outside
    the DB (db.resolve_osm_id)."""
    conn.execute(
        """
        INSERT INTO venue_brand
            (venue_id, brand_id, source, serving, beer, confidence, first_seen, last_seen)
        SELECT ?, brand_id, source, serving, beer, confidence, first_seen, last_seen
        FROM venue_brand WHERE venue_id = ?
        ON CONFLICT(venue_id, brand_id, source, beer) DO UPDATE SET
            serving = CASE WHEN venue_brand.serving = 'unknown'
                             OR (excluded.serving <> 'unknown'
                                 AND excluded.last_seen > venue_brand.last_seen)
                           THEN excluded.serving ELSE venue_brand.serving END,
            first_seen = MIN(venue_brand.first_seen, excluded.first_seen),
            last_seen = MAX(venue_brand.last_seen, excluded.last_seen)
        """, (new["id"], old["id"]))
    for col in ("venue_osm_id", "assigned_osm_id"):
        conn.execute(f"UPDATE submissions SET {col}=? WHERE {col}=?",
                     (new["osm_id"], old["osm_id"]))
    conn.execute("UPDATE osm_rekeys SET new_osm_id=? WHERE new_osm_id=?",
                 (new["osm_id"], old["osm_id"]))
    conn.execute("INSERT OR REPLACE INTO osm_rekeys (old_osm_id, new_osm_id, rekeyed_at) "
                 "VALUES (?, ?, ?)", (old["osm_id"], new["osm_id"], today))
    conn.execute("DELETE FROM venues WHERE id=?", (old["id"],))


def rekey_gone(conn, today: str, dry_run: bool = False, log=print, api=None) -> int:
    """Merge every gone venue that has exactly one survivor and that OSM confirms
    is gone; return how many. All lookups come before the first write.

    Branded venues get a log line for each merge, each element OSM still has,
    and each case with several survivors; brandless ones are only counted (the
    first run has a backlog of them). The branded ones without a matching
    survivor, mostly closed places and the same list every night, are listed on
    a dry run only. An element OSM still has counts as seen today, so it is
    asked about again in GONE_AFTER_DAYS, not every night."""
    moves, ambiguous, unmatched = plan(conn, today)
    moves.sort(key=lambda m: not m[0]["branded"])  # the cap must not starve the map
    confirmed, kept, skipped = [], [], []
    own_api = api is None and bool(moves)
    api = OsmApi() if own_api else api
    try:
        for i, (old, new) in enumerate(moves):
            if i == MAX_LOOKUPS:
                log(f"rekey: {len(moves) - i} left for the next nights (lookup cap)")
                break
            if i:
                time.sleep(LOOKUP_DELAY_S)
            try:
                gone = _gone_from_osm(api, old)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    log(f"rekey: OSM lookup of {old['osm_id']} failed ({exc}); "
                        f"{len(moves) - i} left for the next nights")
                    break
                skipped.append(old)  # 404: never existed (wrong OSM_API_URL?); backed off like a kept one
                log(f"rekey: OSM has no record of {old['osm_id']} (404), skipped for {GONE_AFTER_DAYS} days; check OSM_API_URL")
                continue
            except Exception as exc:  # noqa: BLE001 — any failure: merge nothing on a guess
                log(f"rekey: OSM lookup of {old['osm_id']} failed ({exc}); "
                    f"{len(moves) - i} left for the next nights")
                break
            if gone:
                confirmed.append((old, new))
                if old["branded"]:
                    dist = haversine_m(old["lat"], old["lon"], new["lat"], new["lon"])
                    log(f"rekey: {old['osm_id']} -> {new['osm_id']} ({old['name']}, {dist:.0f} m)")
            else:
                kept.append(old)
                if old["branded"]:
                    log(f"rekey: kept {old['osm_id']} ({old['name']}), OSM still has it")
    finally:
        if own_api:
            api.close()
    if not dry_run:
        for old, new in confirmed:
            merge(conn, old, new, today)
        conn.executemany("UPDATE venues SET updated_at=? WHERE id=?",
                         [(today, v["id"]) for v in kept + skipped])
    for old, found in ambiguous:
        if old["branded"]:
            log(f"rekey: left {old['osm_id']} ({old['name']}), several survivors: "
                + ", ".join(c["osm_id"] for c in found))
    if dry_run:
        for old in unmatched:
            if old["branded"]:
                log(f"rekey: left {old['osm_id']} ({old['name']}), no matching survivor")
    log(f"rekey: {len(confirmed)} {'to re-key' if dry_run else 're-keyed'} "
        f"({sum(o['branded'] for o, _ in confirmed)} with brands), {len(kept)} still in OSM, "
        f"{len(moves) - len(confirmed) - len(kept) - len(skipped)} not asked yet, "
        f"{len(ambiguous)} with several survivors, {len(unmatched)} without a matching one")
    return len(confirmed)


if __name__ == "__main__":
    conn = get_connection(DB_PATH)
    init_db(conn)
    try:
        rekey_gone(conn, date.today().isoformat(), dry_run=True)
    finally:
        conn.close()
