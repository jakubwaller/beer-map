"""Post approved reports OSM is missing as OSM Notes, for a local mapper.

Two kinds of approved report describe something OpenStreetMap does not know
yet: an `add_venue` the duplicate check found no OSM element for (it became a
`community/<slug>` venue on our map), and a `close_venue` for an OSM element.
Neither is edited into the map from here — a visitor's word is not a survey.
Instead each becomes one note at the place, saying what was reported and where
the report came from, so someone who can walk past checks and edits.

Run by hand (`python -m pipeline.osm_notes --dry-run` first), never from cron,
like pipeline.osm_push. Rules:

- One note per submission, at its coordinates, in the local language (Czech in
  CZ, German in DE/AT, English elsewhere), with the honest source: a visitor
  report on zapfkompass.de, or one from a partner app (Dopivatel) forwarded
  there, reviewed by a moderator. Never the visitor's free-text note, IP or
  anything else about them — only the place's name, address and beer.
- Right before posting, OSM is asked again (the dedupe lookup for a new place,
  the element itself for a closure): a mapper may have added or removed it
  since, and then there is nothing left to say.
- Reports older than `--max-age-days` are left alone (not resolved), so a
  wider cutoff can still pick them up later.

`submissions.osm_note_id` records the outcome, like osm_changeset: NULL = still
to post, N > 0 = posted as note N, 0 = resolved without a note (linked to an
OSM element, already in / gone from OSM, a duplicate of a noted report), -1 =
a post that failed after it may have reached OSM (read timeout, unreadable
answer). A -1 row is first looked for among the notes at its point and only
posted again when none carries its text.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config, dedupe
from .db import get_connection, init_db
from .geocode import country_code
from .matching import haversine_m
from .osm_push import _CALL_ERRORS, OsmApi, _status, parse_osm_id, tag_value

SITE = "https://zapfkompass.de"
_LANG = {"cz": "cs", "de": "de", "at": "de"}
_TEXT = {
    "cs": dict(new="Nové místo: {what}", beer="čepované pivo: {beer}",
               closed="Podle hlášení návštěvníka je podnik trvale zavřený: {what}",
               visitor=f"Zdroj: hlášení návštěvníka na {SITE}, zkontrolované moderátorem "
                       "(nejde o místní průzkum).",
               partner=f"Zdroj: hlášení z aplikace {{app}}, předané na {SITE} "
                       "a zkontrolované moderátorem (nejde o místní průzkum).",
               ask="Prosím ověřte na místě a případně upravte mapu."),
    "de": dict(new="Neuer Ort: {what}", beer="Bier vom Fass: {beer}",
               closed="Laut Hinweis eines Besuchers dauerhaft geschlossen: {what}",
               visitor=f"Quelle: Hinweis eines Besuchers auf {SITE}, von einem Moderator "
                       "geprüft (keine Vor-Ort-Begehung).",
               partner=f"Quelle: Meldung aus der App {{app}}, über {SITE} weitergegeben "
                       "und von einem Moderator geprüft (keine Vor-Ort-Begehung).",
               ask="Bitte vor Ort prüfen und die Karte ggf. anpassen."),
    "en": dict(new="New place: {what}", beer="draught beer: {beer}",
               closed="Reported permanently closed by a visitor: {what}",
               visitor=f"Source: a visitor report on {SITE}, reviewed by a moderator "
                       "(not a survey).",
               partner=f"Source: a report from the {{app}} app, forwarded via {SITE} "
                       "and reviewed by a moderator (not a survey).",
               ask="Please check on the ground and update the map if needed."),
}


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def note_text(sub: dict, lang: str, name: str, osm_id: str | None = None) -> str:
    """The note for one submission. Built from the place's name, address and
    beer only — `note`, `submitter_ip` and `ref` never get in."""
    t = _TEXT.get(lang, _TEXT["en"])
    if sub["kind"] == "add_venue":
        what = ", ".join(x for x in (_clean(name), _clean(sub.get("address"))) if x)
        head = t["new"].format(what=what)
        beer = _clean(sub.get("brand"))
        if beer and _clean(sub.get("beer")):
            beer += f" ({_clean(sub['beer'])})"
        if beer:
            head += "; " + t["beer"].format(beer=beer)
    else:
        what = _clean(name) + (f" ({config.OSM_AUTH_URL.rstrip('/')}/{osm_id})" if osm_id else "")
        head = t["closed"].format(what=what)
    partner = sub.get("partner")
    source = t["partner"].format(app=partner.capitalize()) if partner else t["visitor"]
    return f"{head}\n\n{source}\n{t['ask']}"


def pending(conn) -> list[dict]:
    """Approved add_venue/close_venue not yet decided, oldest first, with the
    venue row's coordinates and name for a closure."""
    rows = conn.execute(
        "SELECT s.*, v.lat AS v_lat, v.lon AS v_lon, v.name AS v_name FROM submissions s "
        "LEFT JOIN venues v ON v.osm_id = s.venue_osm_id "
        "WHERE s.status='approved' AND s.kind IN ('add_venue', 'close_venue') "
        "AND (s.osm_note_id IS NULL OR s.osm_note_id = -1) "
        "ORDER BY s.created_at, s.id").fetchall()
    return [dict(r) for r in rows]


def _noted(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT kind, venue_osm_id, venue_name, lat, lon, assigned_osm_id FROM submissions "
        "WHERE osm_note_id > 0").fetchall()]


def _same_report(a: dict, b: dict) -> bool:
    if a["kind"] != b["kind"]:
        return False
    if a["kind"] == "close_venue":
        return a["venue_osm_id"] == b["venue_osm_id"]
    if a.get("assigned_osm_id") and a["assigned_osm_id"] == b.get("assigned_osm_id"):
        return True  # the same community venue
    return (None not in (a["lat"], a["lon"], b["lat"], b["lon"])
            and haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= dedupe.NAME_RADIUS_M
            and dedupe.similar(a["venue_name"], b["venue_name"]))


def _note_url(nid: int) -> str:
    return f"{config.OSM_AUTH_URL.rstrip('/')}/note/{nid}"


def _maybe_sent(exc: Exception) -> bool:
    """False only when the POST surely never created a note: refused with a
    4xx, or no connection made. A 5xx (a proxy may answer after the API
    did), a read timeout or an unreadable 200 may have."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def set_note_id(conn, sub_id: int, note_id: int) -> None:
    conn.execute("UPDATE submissions SET osm_note_id=? WHERE id=?", (note_id, sub_id))
    conn.commit()


def post_notes(conn, api: OsmApi, dry_run: bool = False, max_age_days: int = 90,
               now: datetime | None = None, find_osm=dedupe.osm_candidates,
               country=country_code, log=print, pause_s: float = 1.0) -> dict[str, int]:
    """Post every pending report as a note. In a dry run OSM and Nominatim are
    still asked (the skip rules need them), but nothing is posted or written."""
    counts = dict(posted=0, resolved=0, old=0, failed=0)
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)).isoformat()[:19]
    noted = _noted(conn)

    def resolve(s, note_id, why):
        log(f"#{s['id']} {s['kind']}: {why}")
        if not dry_run:
            set_note_id(conn, s["id"], note_id)
        counts["resolved" if note_id == 0 else "posted"] += 1

    for s in pending(conn):
        if (s["created_at"] or "")[:19] < cutoff:
            counts["old"] += 1
            continue
        if s["kind"] == "add_venue":
            if parse_osm_id(s.get("assigned_osm_id")):
                resolve(s, 0, f"linked to {s['assigned_osm_id']}, already in OSM")
                continue
            lat, lon, name, osm_id = s["lat"], s["lon"], s["venue_name"], None
        else:
            osm_id = s["venue_osm_id"]
            if not parse_osm_id(osm_id):
                resolve(s, 0, f"{osm_id} is not an OSM element, nothing to report")
                continue
            lat, lon = (s["v_lat"], s["v_lon"]) if s["v_lat"] is not None \
                else (s["hint_lat"], s["hint_lon"])
            name = s["v_name"] or s["venue_name"]
        if lat is None or lon is None:
            log(f"#{s['id']} {s['kind']}: no coordinates, skipped")
            counts["failed"] += 1
            continue
        report = dict(kind=s["kind"], venue_osm_id=osm_id, venue_name=name, lat=lat, lon=lon,
                      assigned_osm_id=s.get("assigned_osm_id"))
        if any(_same_report(report, n) for n in noted):
            resolve(s, 0, "the same report is already in a note")
            continue

        if pause_s:
            time.sleep(pause_s)  # OSM and Nominatim etiquette: one place at a time
        try:
            if osm_id is None:
                found = find_osm(name, lat, lon)
                if found:
                    c = found[0]
                    resolve(s, 0, f"OSM has it now: {c['osm_id']} {c['name']!r} {c['dist']} m away")
                    continue
            else:
                el = api.get_element(*parse_osm_id(osm_id))
                name = tag_value(el, "name") or name
                if not any(tag_value(el, k) for k in ("amenity", "shop", "craft")):
                    resolve(s, 0, f"{osm_id} is no longer tagged as a venue on OSM")
                    continue
        except _CALL_ERRORS + (KeyError, TypeError) as exc:
            if _status(exc) in (404, 410):
                resolve(s, 0, f"{osm_id} is gone from OSM")
            else:
                log(f"#{s['id']} {s['kind']}: OSM lookup failed ({exc})")
                counts["failed"] += 1
            continue
        cc = country(lat, lon)
        if cc is None:
            log(f"#{s['id']} {s['kind']}: country lookup failed, skipped")
            counts["failed"] += 1
            continue
        text = note_text(s, _LANG.get(cc, "en"), name, osm_id)
        log(f"#{s['id']} {s['kind']} at {lat:.5f},{lon:.5f} [{cc}]:\n    "
            + text.replace("\n", "\n    "))
        if s["osm_note_id"] == -1:
            try:
                nid = next((i for i, t in api.notes_near(lat, lon) if _clean(t) == _clean(text)),
                           None)
            except _CALL_ERRORS + (KeyError, TypeError) as exc:
                log(f"#{s['id']} {s['kind']}: looking for the earlier post failed ({exc})")
                counts["failed"] += 1
                continue
            if nid is not None:
                resolve(s, nid, f"the earlier post did arrive: {_note_url(nid)}")
                noted.append(report)
                continue
        if dry_run:
            counts["posted"] += 1
        else:
            try:
                nid = api.create_note(lat, lon, text)
            except _CALL_ERRORS + (KeyError, TypeError) as exc:
                sent = _maybe_sent(exc)
                log(f"#{s['id']} {s['kind']}: posting failed ({exc})"
                    + ("; it may have arrived, the next run looks before posting" if sent else ""))
                if sent:
                    set_note_id(conn, s["id"], -1)
                counts["failed"] += 1
                continue
            resolve(s, nid, _note_url(nid))
        noted.append(report)
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true",
                   help="print the notes; post nothing, write nothing")
    p.add_argument("--max-age-days", type=int, default=90,
                   help="leave reports older than this alone (default %(default)s)")
    p.add_argument("--drop", type=int, action="append", default=[],
                   help="mark this submission as not to be posted (repeatable)")
    p.add_argument("--db", default=config.DB_PATH)
    args = p.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    if args.drop:
        for sid in args.drop:
            if not args.dry_run:
                set_note_id(conn, sid, 0)
            print(f"#{sid}: {'would be ' if args.dry_run else ''}dropped")
        return 0
    if not args.dry_run and not config.OSM_TOKEN:
        print("OSM_TOKEN is not set: mint one with `python -m pipeline.osm_auth` "
              "and put it in the environment (deploy/beermap.env on the server)",
              file=sys.stderr)
        return 2
    api = OsmApi(token=config.OSM_TOKEN)
    try:
        counts = post_notes(conn, api, dry_run=args.dry_run, max_age_days=args.max_age_days)
    finally:
        api.close()
    verb = "would post" if args.dry_run else "posted"
    print(f"{verb} {counts['posted']}, resolved without a note {counts['resolved']}, "
          f"older than the cutoff {counts['old']}, failed {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
