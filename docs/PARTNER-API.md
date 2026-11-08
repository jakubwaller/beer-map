# Partner API

A partner app (today: Dopivatel, dopivatel.cz) forwards its players' reports about venues into
the same moderation queue as public submissions. Nothing reaches the map until the owner approves
it at `/admin`, where partner reports carry the partner's name as a tag. This file is the contract
partners build against; change it together with `api/app.py` and `pipeline/submissions.py`.

Unlike a public submission, a partner report sends no notification of its own. The nightly build
(`python -m pipeline.run`, 04:00) sends one digest per night over the usual channels (Telegram, e-mail),
with each partner's pending reports that no digest has announced yet, counted per kind, e.g.
`dopivatel: 3 neue Meldungen (2× add, 1× close_venue)`. With nothing new, it sends nothing.
Each report is marked (`submissions.digested_at`) only after a channel accepted the message, so a
second run the same night stays silent, while a failed send or a skipped night carries over to
the next digest. A report moderated before the digest ran is left out.

## Request

`POST https://zapfkompass.de/api/partner/submit` with a JSON body and
`Authorization: Bearer <secret>`. A container on the host's `web_proxy` Docker network can also
call `http://beermap:8011/api/partner/submit` directly.

Partners and secrets come from the env var `PARTNER_TOKENS`, comma-separated `name:secret` pairs
(`dopivatel:<secret>`, generate one with `openssl rand -hex 32`). The name is what `/admin` and the
nightly digest show. With `PARTNER_TOKENS` unset, every request gets 401.

| Field | Type | Notes |
|---|---|---|
| `ref` | string, 1-64 chars, **required** | The partner's own id for the report. `(partner, ref)` is unique. |
| `kind` | `add` \| `remove` \| `edit_venue` \| `close_venue` \| `add_venue` | Default `add`. |
| `venue_osm_id` | string | OSM style, `node/123`, `way/123`, `relation/123`. Required unless `kind` is `add_venue`, where it is ignored. |
| `venue` | object `{name, lat, lon}` | The venue as the partner knows it. **Required** when `venue_osm_id` is not on Zapfkompass yet (otherwise ignored), and `lat`/`lon` are required for `add_venue`. Coordinates must lie inside the map's bbox (DE, AT, CZ). |
| `brand` | string, 1-80 chars | Required for `add` and `remove`; optional for `add_venue`. |
| `serving` | `fass` \| `tank` \| `unknown` | For `add`, and for `add_venue` with a brand. Default `unknown`. |
| `beer` | string, max 80 chars | Optional specific product, e.g. `"11°"`. On `remove` it removes just that beer; without it the whole brand goes. |
| `name` | string, 1-120 chars | `add_venue`: the new venue's name (required). `edit_venue`: the venue's new name. |
| `address` | string, 1-200 chars | `edit_venue`: the new address (the pin moves to its geocoded spot on approval). `add_venue`: optional. |
| `note` | string, max 300 chars | Shown to the moderator. No player names or other personal data. |

What each kind does once approved:

- `add`: the brand is on tap at the venue (community source, ranked below hand curation).
- `remove`: the brand (or the one `beer`) is no longer on tap.
- `close_venue`: the venue is hidden from the map.
- `edit_venue`: rename (`name`) and/or move (`address`); at least one of the two.
- `add_venue`: creates a new venue at `venue.lat`/`venue.lon` with `name`, plus `brand` if given.
  If the place already exists — on Zapfkompass, or in OSM under a tag the map doesn't import
  (fast_food, stadium, marketplace...) — the moderator may attach the report to that venue instead
  (the brand lands there, no second pin) or reject it as a duplicate.

When `venue_osm_id` is unknown and a `venue` hint comes with it, approval first creates the venue
from the hint (that osm_id, `venue.name`, its coordinates) and then applies the report. The venue
then lives like any other: nightly builds keep it, and the OSM data takes over its name and
position should the sweep ever import that element.

When a mapper deletes one of two elements for the same place, or moves a pub from its building way
onto a new node, the nightly build re-keys the venue: once no import has seen its element for 10
days and exactly one other OSM venue with the same name lies within 50 m, its beers and reports
move to that element, and `venues.json` carries the new `osm_id` from then on. Reports end up on
that venue under either id: one sent with the old id afterwards resolves to it (no `venue` hint
needed), and one sent with the survivor's id before the re-key is merged in with the rest.

## Responses

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"ok": true}` | Queued for moderation. |
| 200 | `{"ok": true, "duplicate": true}` | This `ref` was already received; nothing was created. Safe to retry. |
| 400 | `{"detail": "..."}` | Validation failed. `detail` is a string, or FastAPI's error list when the body has the wrong shape or types. Do not retry unchanged. |
| 401 | `{"detail": "Unauthorized"}` | Token missing or wrong, or the API is switched off. |
| 429 | `{"detail": "partner daily cap exceeded"}` | More than 500 reports from this partner in the last 24 h. Retry later. |

There is no per-IP limit on this endpoint. The 500-per-24-h cap is per partner
(`PARTNER_DAILY_CAP` in `pipeline/config.py`). A duplicate `ref` answers 200 even over the cap.

## Example

```bash
curl -s https://zapfkompass.de/api/partner/submit \
  -H "Authorization: Bearer $SECRET" -H "Content-Type: application/json" \
  -d '{"ref": "report-4711", "kind": "add", "venue_osm_id": "node/123456",
       "venue": {"name": "Hospoda Na Rohu", "lat": 50.0812, "lon": 14.4201},
       "brand": "Kozel", "serving": "tank"}'
```
