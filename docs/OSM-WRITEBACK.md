# Pushing opening-hours corrections to OpenStreetMap

A visitor corrects a venue's hours in the app; a moderator approves it in
`/admin`; the venue row is updated on the spot and the map shows the new
hours the same day. That copy lives only in our database. This runbook is the
second half: carrying the approved correction to OSM, where the hours came
from, so the nightly import brings it back down on its own and everyone else
reading OSM gets it too.

It is done **by hand, with a personal OSM account**, never by cron. Every
upload is an edit under that account's name.

## What one run does

`python -m pipeline.osm_push` takes every approved `edit_hours` submission
that has not been pushed yet and, per venue:

1. fetches the element from the API as it is *now*;
2. skips it if OSM already holds the same hours (spelled differently counts
   as the same: `11:00-24:00` on OSM is `Mo-Su 11:00-24:00` from the grid);
3. refuses it if the element was edited on OSM after the visitor filed the
   report (unless what OSM holds is our own previous upload) — someone else
   got there first and the two edits need a human to compare; refuses it
   likewise if OSM's tag is one the weekday grid could not have shown the
   visitor whole, because the grid's value would replace the whole tag and
   silently drop what the visitor never saw (`--force --id N` to replace
   anyway, `--drop N` to give up). The log names the reason: rules the grid
   cannot express (`PH off`, `Dec 24 off`, seasonal months); a day with more
   than the two ranges it has room for (`Mo-Su 08:00-10:00, Mo-Su
   12:00-14:00, Mo-Su 18:00-22:00`); or, on a tag with a comma-joined rule
   over days already named (`Tu-Su 11:30-14:00, Tu-Sa 17:30-23:00`), a
   report that matches the *older* grid, which showed such a rule as an
   override and so lacked the lunch hours — the report differs from the
   tag exactly on those days. That last one also catches a visitor who
   deliberately edited those days from the new grid; the dry run shows
   both values, and `--force` is the call;
4. opens one changeset for that venue, uploads the element whole with only
   `opening_hours` changed and the fetched `version`, closes the changeset.
   A concurrent edit in the seconds between fetch and upload answers 409
   and the submission stays pending.

Only the newest approved report per venue is uploaded; older ones for the
same venue are marked superseded. A venue without an OSM element (created
from an "Ort fehlt?" submission) or one deleted from OSM is marked resolved.

The outcome is stored on the submission row, `osm_changeset`: `NULL` = still
to push, `N > 0` = uploaded in changeset N, `0` = resolved without an upload.
The nightly build keeps re-applying the submission locally either way; once
the OSM value matches, that is a no-op.

### The changeset

```
created_by = Zapfkompass hours-writeback 1.0
comment    = Update opening_hours of <venue>: hours reported by a visitor on
             zapfkompass.de, reviewed by a moderator before upload
source     = zapfkompass.de visitor report
```

`source` says what actually happened. It is deliberately **not** `survey`:
in OSM that word means a person went and read the sign on the door, and a
false provenance claim is what gets an account's edits reverted. The
visitor's free-text note is not copied into the changeset (anonymous text,
possibly personal data).

These are one-by-one, human-reviewed edits, not a mechanical edit in the
sense of OSM's Automated Edits code of conduct — keep it that way: dry-run
first, look at what it would change, keep the volume to what a person
reviewed. If a mapper objects to an edit, revert it and talk before uploading
more. A line on the account's profile saying it uploads reviewed community
reports from zapfkompass.de saves that conversation.

## Running it

On the VPS, where the database is:

```bash
cd ~/beer-map
docker compose run --rm pipeline python -m pipeline.osm_push --dry-run   # always first
docker compose run --rm pipeline python -m pipeline.osm_push
```

The dry run makes the API reads and prints every planned change without
opening a changeset or touching the database; it needs no token. Options:
`--id N` (only these submissions, repeatable), `--force` (with `--id`:
ignore the edited-after-the-report check), `--drop N` (mark as not to be
uploaded, no network). Exit status is 1 when anything conflicted or failed,
2 when the token is missing.

## One-time: the app and the token

The OAuth 2 application `Zapfkompass` is registered on openstreetmap.org
with the single scope `write_api` and the out-of-band redirect
`urn:ietf:wg:oauth:2.0:oob` (OSM refuses non-HTTPS redirect URIs, so there
is no local callback; the code travels by clipboard). Its client id and
secret live in `~/.zapfkompass-osm.env` on the machine that mints tokens,
mode 600, never in the repo.

Mint the token on a machine with a browser, logged in to the OSM account the
edits should belong to:

```bash
source .venv/bin/activate
python -m pipeline.osm_auth          # prints the URL; approve; paste the code
```

It writes `OSM_TOKEN=…` into that env file and never prints the token. Copy
the line into `deploy/beermap.env` on the VPS (the `pipeline` service reads
it); `python -m pipeline.osm_push --dry-run` there confirms the token works
before anything is written.

OSM tokens do not expire. Revoke at
https://www.openstreetmap.org/oauth2/authorized_applications. OSM shows the
client secret exactly once and has no "regenerate" — rotating it means
deleting the application under https://www.openstreetmap.org/oauth2/applications,
registering it again, updating the env file, and minting a new token.

## Rehearsing against the dev instance

`OSM_API_URL` and `OSM_AUTH_URL` default to the live servers. Point both at
`https://master.apis.dev.openstreetmap.org` (separate accounts, separate app
registration, data thrown away periodically) to watch a real upload happen
without touching the live map.
