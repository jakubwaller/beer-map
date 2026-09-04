"""Push approved opening-hours corrections upstream to OpenStreetMap.

The in-app editor stores a visitor's correction on the venue row the moment a
moderator approves it; that copy is what the map shows the same day. This
script carries the same correction to OSM afterwards, so the nightly import
brings it back down on its own and every other OSM consumer gets it too.

It is a one-shot, run by hand (`python -m pipeline.osm_push --dry-run` first),
never a pipeline step: each upload is an edit under a real OSM account, and
the person holding that account decides when it happens.

Rules, all of which exist because they were the cheap way to be a good OSM
citizen:

- One venue per changeset, tagged with the real provenance: a visitor report
  on zapfkompass.de reviewed by a moderator. Never `source=survey` — nobody
  went and read the sign on the door.
- The element is fetched right before the upload and sent back whole, with
  only `opening_hours` changed and the fetched `version`, so a concurrent
  edit answers 409 instead of being overwritten.
- An element edited on OSM *after* the visitor filed the report is not
  touched (unless what OSM holds is our own previous upload): someone else
  got there first, and the two edits need a human to compare. Neither is a
  tag the grid could not have shown the visitor whole: rules it cannot
  express (`PH off`, `Dec 24 off`, ...) or a day with more than the two
  ranges it has room for. The grid's value would replace such a tag
  wholesale and silently drop what the visitor never saw.
  `--force --id N` overrides either for one submission, `--drop N` gives
  up on it.
- A value OSM already holds (or one that means the same hours, e.g. the tag
  `11:00-24:00` against our `Mo-Su 11:00-24:00`) is not re-uploaded.

`submissions.osm_changeset` records the outcome: NULL = still to push, N > 0
= uploaded in changeset N, 0 = resolved without an upload (already in OSM,
venue not an OSM element or gone from OSM, or superseded by a newer report
for the same venue).
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

from . import config
from .db import (get_connection, get_submission, init_db, list_hours_for_osm,
                 set_submission_changeset)

CREATED_BY = "Zapfkompass hours-writeback 1.0"
_OSM_ID_RE = re.compile(r"^(node|way|relation)/(\d+)$")
# Server-side bookkeeping OSM sets on the way out; it is ignored (or refused)
# on the way back in, so the upload body carries none of it.
_STRIP_ATTRS = ("timestamp", "user", "uid", "visible")
# Changeset tag values are capped at 255 characters; a venue name that long
# would push the comment over.
_NAME_MAX = 80


def parse_osm_id(osm_id: str | None) -> tuple[str, int] | None:
    """`node/373451004` -> ("node", 373451004); None for the venues the site
    created itself (`community/<slug>`, `manual/<slug>`), which have no OSM
    element to write to."""
    m = _OSM_ID_RE.match(osm_id or "")
    return (m.group(1), int(m.group(2))) if m else None


def changeset_tags(venue_name: str) -> dict[str, str]:
    """The changeset's own tags. `source` and `comment` say where the hours
    really come from — a form on zapfkompass.de, checked by a moderator —
    because a wrong provenance claim is what gets an account's edits
    reverted."""
    name = " ".join((venue_name or "").split())[:_NAME_MAX] or "a venue"
    return {
        "created_by": CREATED_BY,
        "comment": (f"Update opening_hours of {name}: hours reported by a visitor "
                    "on zapfkompass.de, reviewed by a moderator before upload"),
        "source": "zapfkompass.de visitor report",
    }


def _changeset_xml(tags: dict[str, str]) -> str:
    osm = ET.Element("osm")
    cs = ET.SubElement(osm, "changeset")
    for k, v in tags.items():
        ET.SubElement(cs, "tag", k=k, v=v)
    return ET.tostring(osm, encoding="unicode")


def read_element(xml: str, kind: str) -> ET.Element:
    """The `<node>`/`<way>`/`<relation>` inside an API answer."""
    el = ET.fromstring(xml).find(kind)
    if el is None:
        raise ValueError(f"no <{kind}> in the API answer")
    return el


def tag_value(el: ET.Element, key: str) -> str | None:
    for t in el.findall("tag"):
        if t.get("k") == key:
            return t.get("v")
    return None


def with_opening_hours(el: ET.Element, hours: str, changeset: int) -> str:
    """The upload body: the fetched element, whole, with `opening_hours` set
    and the changeset filled in. Everything else — the other tags, a way's
    node refs, a relation's members, and above all the `version` — is sent
    back exactly as fetched, which is what makes the server refuse the
    upload (409) if the element moved on in between."""
    el = copy.deepcopy(el)
    for attr in _STRIP_ATTRS:
        el.attrib.pop(attr, None)
    el.set("changeset", str(changeset))
    tag = next((t for t in el.findall("tag") if t.get("k") == "opening_hours"), None)
    if tag is None:
        tag = ET.SubElement(el, "tag", k="opening_hours")
    tag.set("v", hours)
    osm = ET.Element("osm")
    osm.append(el)
    return ET.tostring(osm, encoding="unicode")


# --- "means the same hours" ------------------------------------------------
# A visitor who opens the editor and saves without touching a day still sends
# the grid's canonical spelling, which differs from what OSM holds as often
# as not (`11:00-24:00` on OSM is `Mo-Su 11:00-24:00` from the grid). Reading
# both into per-day minute ranges catches that and saves OSM a no-op edit.
# This is deliberately a small, conservative reader: anything it cannot read
# compares as "different", and the upload goes ahead.

_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_TIME_RE = re.compile(r"^(\d\d):(\d\d)$")
_SEL_RE = re.compile(r"^(?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?)"
                     r"(?:,(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?)*$")


def _minutes(s: str) -> int | None:
    m = _TIME_RE.match(s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if mi > 59 or h > 24 or (h == 24 and mi > 0):
        return None
    return h * 60 + mi


def _day_set(sel: str) -> set[int] | None:
    if not _SEL_RE.match(sel):
        return None
    days: set[int] = set()
    for part in sel.split(","):
        a, _, b = part.partition("-")
        i, j = _DAYS.index(a), _DAYS.index(b or a)
        days.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
    return days


def _rules(value: str) -> list[list[str]]:
    """Split a tag into its rules: one group per `;`, holding the rules a `,`
    joins inside it. `;` is the separator, but mappers routinely write `,`
    for it ("Su-Th 18:00-01:00, Fr,Sa 18:00-02:00") — the same reading as
    splitRules in web/hours.js: a comma starts a new rule only when what
    came before already holds a time and what follows names a day, which
    leaves genuine lists ("12:00-15:00,17:30-22:00", "Mo-Su,PH 11:00-23:00")
    intact. Whitespace around commas is dropped first, so a raw tag reads
    the same as a tidied one."""
    value = re.sub(r"\s*,\s*", ",", " ".join(value.split()))
    groups: list[list[str]] = []
    for chunk in value.split(";"):
        group: list[str] = []
        buf = ""
        for part in chunk.split(","):
            if buf and re.search(r"\d", buf) and re.match(r"[A-Za-z]{2}\b", part):
                group.append(buf.strip())
                buf = part
            else:
                buf = f"{buf},{part}" if buf else part
        if buf.strip():
            group.append(buf.strip())
        if group:
            groups.append(group)
    return groups


def _read_rule(rule: str) -> tuple[set[int], list[tuple[int, int]] | None] | None:
    """One rule -> (days, ranges); ranges None means `off`/`closed`, the whole
    thing None means beyond the subset."""
    head, _, rest = rule.partition(" ")
    if _SEL_RE.match(head):
        which = _day_set(head)
        spec = rest.strip()
    else:  # no selector: the rule covers every day
        which, spec = set(range(7)), rule
    if which is None or not spec:
        return None
    if spec in ("off", "closed"):
        return which, None
    ranges: list[tuple[int, int]] = []
    for r in spec.split(","):
        a, _, b = r.partition("-")
        start, end = _minutes(a), _minutes(b)
        if start is None or end is None or start >= 1440:
            return None
        if end <= start:  # closes after midnight (or at it: "11:00-00:00")
            end += 1440
        ranges.append((start, end))
    # By start only, and stable, exactly as parseRanges in web/hours.js: the
    # overlap rule is order-sensitive, so the two must feed it the same order.
    return which, sorted(ranges, key=lambda r: r[0])


def _add_ranges(have: list[tuple[int, int]], more: list[tuple[int, int]]) -> list | None:
    """The ranges a comma-joined rule adds to a day its group already named —
    the same rule as addRanges in web/hours.js. One the day already has counts
    once. One that covers whatever it overlaps ("Mo-Fr 15:00-01:00, Fr,Sa
    15:00-03:00": Friday's is extended) replaces it — adding and overriding
    agree there. Any other overlap is ambiguous, and None."""
    out = list(have)
    for r in more:
        if r in out:
            continue
        hit = [x for x in out if r[0] < x[1] and x[0] < r[1]]
        if any(r[0] > x[0] or r[1] < x[1] for x in hit):
            return None
        out = [x for x in out if x not in hit] + [r]
    return sorted(out, key=lambda r: r[0])  # by start only, stable, as addRanges


def normalize_hours(value: str | None) -> tuple | None:
    """A tag -> seven tuples of (start, end) minute pairs, or None when the
    tag uses anything beyond weekdays, clock ranges, `off`/`closed` and 24/7.
    A range ending at 00:00 is read as ending at 24:00, so both spellings of
    "until midnight" compare equal. "Mo, Tu, Su 17:00-00:00" reads like
    "Mo,Tu,Su …" — valid syntax and common (the first live report, 2026-09-03,
    hit exactly that spelling and was refused as inexpressible).

    `;` overrides the days it names, `,` adds to them: "Tu-Su 11:30-14:00,
    Tu-Sa 17:30-23:00" keeps the lunch hours on Tu-Sa, a restated range
    counts once, a comma-joined `off` still closes its day, one that extends
    a range ("Mo-Fr 15:00-01:00, Fr,Sa 15:00-03:00") replaces it, and any
    other overlap in time ("Mo-Su 11:00-23:00, Su 12:00-20:00" — as often
    meant as an override) is out of scope. This is the reading of
    web/hours.js, deliberately: the grid it prefills is what a visitor saves,
    and this reader is what that grid is compared against — read the tag
    differently and an untouched grid comes back as an edit, one that
    deletes from OSM whatever the two readings disagree on."""
    if not value:
        return None
    if " ".join(value.split()) == "24/7":
        return tuple(((0, 1440),) for _ in range(7))
    days: list[list[tuple[int, int]]] = [[] for _ in range(7)]
    for group in _rules(value):
        given: dict[int, list[tuple[int, int]]] = {}
        for rule in group:
            read = _read_rule(rule.strip())
            if read is None:
                return None
            which, ranges = read
            for d in which:
                if ranges is None:
                    given[d] = []
                    continue
                joined = _add_ranges(given[d], ranges) if d in given else list(ranges)
                if joined is None:
                    return None
                given[d] = joined
        for d, r in given.items():
            days[d] = r
    return tuple(tuple(sorted(d)) for d in days)


def grid_can_hold(value: str) -> bool:
    """Whether the weekday grid could have shown a visitor this tag whole:
    readable, and no day with more than the two ranges the grid has room
    for (web/app.js leaves the grid blank for a third)."""
    days = normalize_hours(value)
    return days is not None and all(len(d) <= 2 for d in days)


# Until PR #63 went live, web/hours.js read a comma-joined rule over a day
# its group had already named as an override, and dropped the earlier hours
# from the grid it prefilled. A report filed before then on such a tag came
# from a grid that lacked hours the tag has, and the push at the time refused
# it. Keep refusing those: the reading changed, what the visitor saw did not.
_GRID_ADDS_SINCE = "2026-09-05"


def _adds_to_named_days(value: str) -> bool:
    """Whether any `;` group of the tag has a comma-joined rule over a day the
    group already named — the tags whose reading PR #63 changed."""
    for group in _rules(value):
        named: set[int] = set()
        for rule in group:
            read = _read_rule(rule.strip())
            if read is None:
                return False  # unreadable anyway; grid_can_hold says so
            if named & read[0]:
                return True
            named |= read[0]
    return False


def grid_showed_whole(current: str, filed: str) -> bool:
    """Whether the grid the visitor filled in was prefilled with all of
    OSM's tag — as the grid read it when the report was filed."""
    if not grid_can_hold(current):
        return False
    return filed >= _GRID_ADDS_SINCE or not _adds_to_named_days(current)


def same_hours(a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    na, nb = normalize_hours(a), normalize_hours(b)
    return na is not None and na == nb


# --- the API ----------------------------------------------------------------

class OsmApi:
    """The four calls an upload needs, against api.openstreetmap.org unless
    `OSM_API_URL` says otherwise (the dev instance for a rehearsal). The
    token is the personal OAuth 2 token `pipeline.osm_auth` mints."""

    def __init__(self, base_url: str | None = None, token: str = "", transport=None):
        self.base = (base_url or config.OSM_API_URL).rstrip("/")
        headers = {"User-Agent": config.USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, timeout=60, transport=transport)

    def get_element(self, kind: str, oid: int) -> ET.Element:
        r = self.client.get(f"{self.base}/api/0.6/{kind}/{oid}")
        r.raise_for_status()
        return read_element(r.text, kind)

    def create_changeset(self, tags: dict[str, str]) -> int:
        r = self.client.put(f"{self.base}/api/0.6/changeset/create",
                            content=_changeset_xml(tags),
                            headers={"Content-Type": "text/xml"})
        r.raise_for_status()
        return int(r.text.strip())

    def upload(self, kind: str, oid: int, body: str) -> int:
        """Returns the element's new version."""
        r = self.client.put(f"{self.base}/api/0.6/{kind}/{oid}", content=body,
                            headers={"Content-Type": "text/xml"})
        r.raise_for_status()
        return int(r.text.strip())

    def close_changeset(self, cid: int) -> None:
        r = self.client.put(f"{self.base}/api/0.6/changeset/{cid}/close")
        r.raise_for_status()

    def close(self) -> None:
        self.client.close()


def changeset_url(cid: int) -> str:
    return f"{config.OSM_AUTH_URL.rstrip('/')}/changeset/{cid}"


# --- the run ----------------------------------------------------------------

def plan(subs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split pending corrections into the ones to upload and the ones a newer
    report for the same venue has superseded. `subs` is oldest first, as
    list_hours_for_osm returns it; the local apply runs in the same order, so
    the last report per venue is also what the map shows."""
    latest: dict[str, int] = {}
    for s in subs:
        latest[s["venue_osm_id"]] = s["id"]
    keep = set(latest.values())
    return ([s for s in subs if s["id"] in keep],
            [s for s in subs if s["id"] not in keep])


def _status(exc: Exception) -> int | None:
    return exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None


# What an upload can throw: transport and status errors, and a 200 whose body
# is not what the API sends (a proxy page) — read_element and int() raise
# ValueError on those, and a whole batch must not die of one.
_CALL_ERRORS = (httpx.HTTPError, ValueError)


def _venue_name(conn, el: ET.Element, osm_id: str) -> str:
    """For the changeset comment: OSM's own `name` tag first, then our venue
    row. Not the submission's `venue_name` — the API stores the OSM id there
    for every kind but add_venue."""
    name = tag_value(el, "name")
    if name:
        return name
    row = conn.execute("SELECT name FROM venues WHERE osm_id=?", (osm_id,)).fetchone()
    return (row["name"] if row else None) or osm_id


def _last_pushed(conn, osm_id: str) -> dict | None:
    """The newest correction for this venue that reached OSM, if any."""
    row = conn.execute(
        "SELECT * FROM submissions WHERE kind='edit_hours' AND status='approved' "
        "AND venue_osm_id=? AND osm_changeset > 0 ORDER BY created_at DESC, id DESC LIMIT 1",
        (osm_id,),
    ).fetchone()
    return dict(row) if row else None


def push(conn, api: OsmApi, subs: list[dict] | None = None, dry_run: bool = False,
         force: bool = False, log=print, pause_s: float = 1.0) -> dict[str, int]:
    """Upload every pending correction (or just `subs`). Returns counts per
    outcome; `uploaded` counts would-be uploads in a dry run, which makes no
    request beyond the GETs and writes nothing to the DB."""
    counts = dict(uploaded=0, unchanged=0, superseded=0, skipped=0, conflict=0, failed=0)

    def resolve(sub, changeset: int):
        if not dry_run:
            set_submission_changeset(conn, sub["id"], changeset)

    todo, superseded = plan(list_hours_for_osm(conn) if subs is None else subs)
    for s in superseded:
        log(f"#{s['id']} {s['venue_osm_id']}: superseded by a newer report for the same venue")
        resolve(s, 0)
        counts["superseded"] += 1

    for s in todo:
        sid, osm_id, target = s["id"], s["venue_osm_id"], s["opening_hours"]
        ref = parse_osm_id(osm_id)
        if ref is None:
            log(f"#{sid} {osm_id}: not an OSM element, nothing to push")
            resolve(s, 0)
            counts["skipped"] += 1
            continue
        kind, oid = ref
        filed = (s.get("created_at") or "")[:19]
        pushed = _last_pushed(conn, osm_id)
        if pushed and ((pushed["created_at"] or "")[:19], pushed["id"]) > (filed, sid):
            log(f"#{sid} {osm_id}: superseded by #{pushed['id']}, already in "
                f"{changeset_url(pushed['osm_changeset'])}")
            resolve(s, 0)
            counts["superseded"] += 1
            continue
        try:
            el = api.get_element(kind, oid)
        except _CALL_ERRORS as exc:
            code = _status(exc)
            if code in (404, 410):
                log(f"#{sid} {osm_id}: gone from OSM ({code}), nothing to push")
                resolve(s, 0)
                counts["skipped"] += 1
            else:
                log(f"#{sid} {osm_id}: fetch failed ({exc})")
                counts["failed"] += 1
            continue

        current = tag_value(el, "opening_hours")
        if same_hours(current, target):
            log(f"#{sid} {osm_id}: OSM already has these hours ({current!r})")
            resolve(s, 0)
            counts["unchanged"] += 1
            continue
        edited = (el.get("timestamp") or "")[:19]
        # A later edit on OSM is someone else's work to compare by hand —
        # unless what OSM holds is our own previous upload for this venue.
        ours = pushed is not None and same_hours(current, pushed["opening_hours"])
        how = f"Re-run with --force --id {sid} to replace it anyway, --drop {sid} to let it go"
        if edited > filed and not ours and not force:
            log(f"#{sid} {osm_id}: edited on OSM at {edited} after the report ({filed}); "
                f"OSM has {current!r}, the report says {target!r}. {how}")
            counts["conflict"] += 1
            continue
        # The grid holds two ranges a day and prefills nothing for a third
        # (web/app.js), so a tag the reader can read may still be one the
        # grid could not show the visitor — or could not at the time.
        if current and not grid_showed_whole(current, filed) and not force:
            log(f"#{sid} {osm_id}: OSM has {current!r}, with rules the grid cannot express; "
                f"uploading {target!r} would drop them. {how}")
            counts["conflict"] += 1
            continue

        name = _venue_name(conn, el, osm_id)
        log(f"#{sid} {osm_id} ({name}): {current!r} -> {target!r}")
        if dry_run:
            counts["uploaded"] += 1
            continue
        try:
            cid = api.create_changeset(changeset_tags(name))
        except _CALL_ERRORS as exc:
            log(f"#{sid} {osm_id}: creating the changeset failed ({exc})")
            counts["failed"] += 1
            continue
        try:
            version = api.upload(kind, oid, with_opening_hours(el, target, cid))
        except _CALL_ERRORS as exc:
            # 409 is the version check doing its job: the element changed
            # between our GET and PUT. Anything else is just a failure.
            what = "conflict" if _status(exc) == 409 else "failed"
            log(f"#{sid} {osm_id}: upload {what} ({exc})")
            counts[what] += 1
            continue
        finally:
            # Whatever happened — a refused upload, an unreadable answer, a
            # Ctrl-C — the changeset must not stay open.
            _close_quietly(api, cid, log)
        resolve(s, cid)
        log(f"    -> version {version}, {changeset_url(cid)}")
        counts["uploaded"] += 1
        if pause_s:
            time.sleep(pause_s)
    return counts


def _close_quietly(api: OsmApi, cid: int, log) -> None:
    try:
        api.close_changeset(cid)
    except httpx.HTTPError as exc:
        # The server closes an idle changeset after an hour by itself; this
        # only means the id is worth a look.
        log(f"    closing changeset {cid} failed ({exc}); it closes itself within the hour")


def _select(conn, ids: list[int], log) -> list[dict]:
    """The pending corrections among `ids`, explaining each one that is not."""
    pending = {s["id"]: s for s in list_hours_for_osm(conn)}
    chosen = []
    for sid in ids:
        if sid in pending:
            chosen.append(pending[sid])
            continue
        s = get_submission(conn, sid)
        if s is None:
            why = "no such submission"
        elif s["kind"] != "edit_hours":
            why = f"kind is {s['kind']}, not edit_hours"
        elif s["status"] != "approved":
            why = f"status is {s['status']}, not approved"
        elif s.get("osm_changeset"):
            why = f"already uploaded, {changeset_url(s['osm_changeset'])}"
        else:
            why = "already resolved without an upload"
        log(f"#{sid}: {why}")
    return chosen


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be uploaded; no changesets, no DB writes")
    p.add_argument("--id", type=int, action="append", default=[],
                   help="only this submission (repeatable)")
    p.add_argument("--force", action="store_true",
                   help="with --id: upload even if OSM was edited after the report")
    p.add_argument("--drop", type=int, action="append", default=[],
                   help="mark this submission as not to be uploaded (repeatable)")
    p.add_argument("--db", default=config.DB_PATH)
    args = p.parse_args(argv)
    if args.force and not args.id:
        p.error("--force needs --id: it overrides the conflict check for named submissions only")

    conn = get_connection(args.db)
    init_db(conn)
    for sid in args.drop:
        for s in _select(conn, [sid], print):
            if args.dry_run:
                print(f"#{sid}: would be dropped")
            else:
                set_submission_changeset(conn, s["id"], 0)
                print(f"#{sid}: dropped, will not be uploaded")
    if args.drop and not args.id:
        return 0

    if not args.dry_run and not config.OSM_TOKEN:
        print("OSM_TOKEN is not set: mint one with `python -m pipeline.osm_auth` "
              "and put it in the environment (deploy/beermap.env on the server)",
              file=sys.stderr)
        return 2
    subs = _select(conn, args.id, print) if args.id else None
    if args.id and not subs:
        return 1
    api = OsmApi(token=config.OSM_TOKEN)
    try:
        counts = push(conn, api, subs, dry_run=args.dry_run, force=args.force)
    finally:
        api.close()
    verb = "would upload" if args.dry_run else "uploaded"
    print(f"{verb} {counts['uploaded']}, unchanged {counts['unchanged']}, "
          f"superseded {counts['superseded']}, skipped {counts['skipped']}, "
          f"conflict {counts['conflict']}, failed {counts['failed']}")
    return 1 if counts["conflict"] or counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
