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

Gone: a visible OSM venue no import has seen for GONE_AFTER_DAYS. Survivor: a
visible OSM venue an import did see in that window, with the same fold()ed
name, within RADIUS_M. With exactly one survivor the gone venue is merged into
it; with none or several it is left alone. The sweeps import every named
pub/bar/biergarten/restaurant/cafe, so a survivor is already a venue row and a
re-key is always a merge. Hidden (closed) venues take no part: they are off the
map, and carrying a closure over would hide the survivor.

Runs in every nightly build (pipeline/run.py), before curation and the
community re-apply, so both land on the survivor the same night.
`python -m pipeline.rekey` prints what the next build will do and writes nothing.
"""
from __future__ import annotations

from datetime import date, timedelta

from .config import DB_PATH
from .db import fold, get_connection, init_db
from .dedupe import _bbox
from .matching import haversine_m

# The weekly country sweep (Sunday 05:00) refreshes every venue in DE/CZ/AT, so
# a live one is at most 7 days old at the 04:00 build before it; 10 leaves room
# for a sweep that ends late. A sweep that fails ages the survivors along with
# the rest, so it stalls re-keying instead of misfiring.
GONE_AFTER_DAYS = 10
# On the 2026-11-08 data every branded gone venue with a same-name survivor had
# it within 39 m, most within 5 m (a node dropped onto the building way it
# replaced); 150 m found no more. Ways and relations sit at their centre, as the
# import stores them, so a node and a way of one place compare like two nodes.
RADIUS_M = 50

_LIVE = ("(osm_id LIKE 'node/%' OR osm_id LIKE 'way/%' OR osm_id LIKE 'relation/%') "
         "AND COALESCE(hidden, 0) = 0")


def plan(conn, today: str):
    """(moves, ambiguous, unmatched) as of `today`: moves are (gone, survivor)
    venue rows, ambiguous are (gone, [survivors]), unmatched are gone rows."""
    cutoff = (date.fromisoformat(today) - timedelta(days=GONE_AFTER_DAYS)).isoformat()
    gone = conn.execute(
        "SELECT id, osm_id, name, lat, lon, EXISTS (SELECT 1 FROM venue_brand vb "
        "WHERE vb.venue_id = venues.id) AS branded "
        f"FROM venues WHERE {_LIVE} AND updated_at < ? ORDER BY id", (cutoff,)).fetchall()
    moves, ambiguous, unmatched = [], [], []
    for v in gone:
        key = fold(v["name"])  # '' (a name in another script) matches nothing
        s, w, n, e = _bbox(v["lat"], v["lon"], RADIUS_M)
        found = [c for c in conn.execute(
            "SELECT id, osm_id, name, lat, lon FROM venues WHERE lon BETWEEN ? AND ? "
            f"AND lat BETWEEN ? AND ? AND {_LIVE} AND updated_at >= ? ORDER BY id",
            (w, e, s, n, cutoff))
            if key and fold(c["name"]) == key
            and haversine_m(v["lat"], v["lon"], c["lat"], c["lon"]) <= RADIUS_M]
        if len(found) == 1:
            moves.append((v, found[0]))
        elif found:
            ambiguous.append((v, found))
        else:
            unmatched.append(v)
    return moves, ambiguous, unmatched


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


def rekey_gone(conn, today: str, dry_run: bool = False, log=print) -> int:
    """Merge every gone venue that has exactly one survivor; return how many.

    Each branded venue merged or left with several survivors gets a log line;
    brandless ones are only counted (the first run has a backlog of them). The
    branded ones without any survivor, mostly closed places and the same list
    every night, are listed on a dry run only."""
    moves, ambiguous, unmatched = plan(conn, today)
    for old, new in moves:
        if old["branded"]:
            dist = haversine_m(old["lat"], old["lon"], new["lat"], new["lon"])
            log(f"rekey: {old['osm_id']} -> {new['osm_id']} ({old['name']}, {dist:.0f} m)")
        if not dry_run:
            merge(conn, old, new, today)
    for old, found in ambiguous:
        if old["branded"]:
            log(f"rekey: left {old['osm_id']} ({old['name']}), several survivors: "
                + ", ".join(c["osm_id"] for c in found))
    if dry_run:
        for old in unmatched:
            if old["branded"]:
                log(f"rekey: left {old['osm_id']} ({old['name']}), no survivor")
    log(f"rekey: {len(moves)} {'to re-key' if dry_run else 're-keyed'} "
        f"({sum(o['branded'] for o, _ in moves)} with brands), "
        f"{len(ambiguous)} with several survivors, {len(unmatched)} gone without one")
    return len(moves)


if __name__ == "__main__":
    conn = get_connection(DB_PATH)
    init_db(conn)
    try:
        rekey_gone(conn, date.today().isoformat(), dry_run=True)
    finally:
        conn.close()
