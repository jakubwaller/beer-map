# CLAUDE.md

Guidance for coding agents working in this repository.

## What this is

A map of German, Austrian and Czech drinking venues filterable by draft beer brand and serving type (Fassbier/Tankbier); every venue in all three countries is in the DB (`pipeline/country.py` sweeps nationwide, tile by tile), branded venues are exported to GeoJSON, and the brandless majority is served per viewport via `/api/gray`. A Python pipeline builds a SQLite DB + GeoJSON export; a static vanilla-JS frontend renders it; a FastAPI app adds tile/search endpoints and anonymous submissions with a moderation queue. Deployed as a Docker container behind Caddy on a Linux VPS (it ran on a Raspberry Pi until August 2026 — older notes still say "the Pi").

## Commands

```bash
source .venv/bin/activate            # venv with requirements.txt installed

pytest -v                            # Python pipeline + API tests
pytest tests/test_matching.py -v     # one file
pytest tests/test_api.py::test_submit_ok -v   # one test
node --test web/*.test.js            # frontend pure-function tests (Node)

python -m pipeline.run               # full dataset rebuild -> web/data/venues.json
python -m pipeline.country           # nationwide venue sweep (weekly; --resume to continue)
python -m http.server -d web 8000    # serve frontend only (no API)
uvicorn api.app:app                  # serve frontend + API on one origin
python -m pipeline.export_curation   # print approved community subs as curation.yaml entries

./docker-run.sh                      # build + start container + rebuild dataset (deploy)
```

Code targets Python 3.9+ — every module starts with `from __future__ import annotations`; keep union type hints (`str | None`) behind it.

## Architecture

### Trust hierarchy (the core concept)

Every venue↔brand link (`venue_brand` edge) records its `source`, and sources are ranked: `manual` (curation.yaml) > `community` (approved submissions) > `finder:<brand>` (scraped brand locators) > `osm` (`brewery=` tags). This ranking is implemented **twice and must stay in sync**: the SQL `ORDER BY CASE` in `fetch_venues_with_brands` (`pipeline/db.py`) and `rank()` in `web/datasource.js` (its `dedupeBrands` assumes best-trust-first order).

### Pipeline (`pipeline/run.py`)

One idempotent build, run nightly via cron and by `docker-run.sh`:

1. **OSM**: fetch the sweep cities' pubs/bars (plus brewery-tagged venues nationwide) via Overpass (`osm.py` — tries mirror list `OVERPASS_URLS` with retry/backoff on transient statuses), upsert venues, extract edges from `brewery=` tags. The `opening_hours` tag rides along on the venue row and into the export (OSM is its only source — nothing else writes it). Two kinds of answer that look like success are refused there: one from a database older than `OVERPASS_MAX_DATA_AGE_H` (read off `osm3s.timestamp_osm_base` — in August 2026 private.coffee was 102 days behind and kumi 23, both HTTP 200) and one carrying a timeout `remark`. Neither is retried on the same mirror; the next one gets the query. Before each request to a host in `OVERPASS_STATUS_HOSTS`, `/api/status` is read and the announced slot wait slept off — the main instance holds a used slot ~40 s no matter how short the query, so firing back to back only collects 429s.
2. **Finders**: per-brand "where to drink" scrapers (`pipeline/finders/`), fuzzy-matched to OSM venues by name + distance (`matching.py`, rapidfuzz, 85 threshold / 120 m). A failing finder logs a WARN and never kills the build.
3. **Curation**: `curation.yaml` entries applied as `source="manual"` (`curation.py`). Entries resolve a venue by `osm_id`, by `lat`+`lon` (creates a `manual/<slug>` venue), or by fuzzy name. An entry without `brand` just pins the venue (gray dot) — for places the amenity sweep can't see, e.g. tagged `shop=alcohol`.
4. **Community**: all approved submissions re-applied (`submissions.apply_approved`) — this is why approved venue edits/closures survive the OSM re-import each build.
5. **Export**: GeoJSON to `web/data/venues.json` (`export.py`) — **branded venues only**. Hidden (closed) venues stay in the DB but are excluded. Brandless venues are never exported; `/api/gray/{z}/{x}/{y}` serves them per slippy tile straight off the DB.

Separately, `pipeline/country.py` sweeps every venue in DE+CZ+AT into the same `venues` table: a grid of 1° bbox tiles (a single country-wide Overpass query times out), each clipped to the country areas, quartered on failure, recorded in `country_tiles` for `--resume`. It runs weekly; the nightly run never deletes its venues (everything is upserts).

Everything is upserts keyed on `(venue_id, brand_id, source, beer)` — `beer` (specific product, `''` = brand-only) is part of the PK so one venue can list several beers of a brand. Schema migrations are hand-rolled in `pipeline/db.py` (`_MIGRATIONS` + `_migrate_venue_brand_pk`); the DB was created with `CREATE TABLE IF NOT EXISTS`, so new columns must be added there too.

### API (`api/app.py`)

`create_app()` builds a FastAPI app that also mounts `web/` as static files (single origin). `GET /api/gray/{z}/{x}/{y}` returns the brandless venues of one slippy tile (z 8–14, browser-cacheable for an hour); `GET /api/search?q=` searches the whole venue table over the folded `search_key` column — `fold()` in `pipeline/db.py` MUST stay in sync with `fold()` in `web/datasource.js`, the client compares against the same folded strings. `POST /api/submit` takes anonymous add/remove/edit_venue/close_venue/add_venue submissions (honeypot field + per-IP rate limit); `/admin` is an HTTP-Basic (`BEERMAP_ADMIN_PW`) moderation page. Approving applies the change immediately and re-exports the GeoJSON — no pipeline run needed. `edit_venue` geocodes the new address via Nominatim (`geocode.py`) to move the pin; geocode failure falls back to text-only update. `add_venue` (the "Ort fehlt?" form) geocodes at approval time and creates a `community/<slug>` venue; the hit is stored on the submission row so nightly re-applies don't re-geocode, and a submission that can't be applied (venue gone, address not geocodable) stays pending instead of being approved into a no-op. Client IP comes from `X-Forwarded-For` via ProxyHeadersMiddleware — safe only because the container has no public port and Caddy is the sole ingress.

`pipeline/export_curation.py` renders approved submissions as curation.yaml entries so they can be committed to git and survive DB loss.

### Frontend (`web/`)

No build step, no npm deps: vanilla ES modules + vendored MapLibre (`web/vendor/`). The pure functions live in `datasource.js` (load/dedupe/filter, the folded token-scored venue search, and the brand chip/picker lists) and `hours.js` (the OSM `opening_hours` subset parser, "open now" and the German week view) — those two are the unit-tested part, via `node --test web/*.test.js`. `app.js` does the map, filter UI, search dropdown, brand autocomplete, and submission forms.

Three filters combine: the serving group (all/draught/fass/tank), a brand, and the "Jetzt geöffnet" toggle. Open-now drops every venue whose `opening_hours` is missing or outside the parser's scope — "we don't know" is not a yes — and re-checks itself every minute, since it is the one filter that goes stale while the map just sits there. Only ~9 of the ~1500 brands fit the chip bar; the rest live behind the "Alle Marken" chip, whose picker searches all of them and puts the chosen one in front of the bar so it stays visible and switch-off-able.

Two mobile-Safari rules the UI depends on: form inputs are ≥16px on small screens (anything smaller makes iOS zoom the page on focus), and suggestion lists are hand-rolled rather than `<datalist>`, which mobile Safari renders erratically.

`web/og-image.jpg` is the social preview card referenced by the `og:`/`twitter:` meta in `index.html`; regenerate it with `docs/og-image.md`. Venue names/addresses/brands come from OSM (publicly editable), so **every interpolated value must go through `esc()`** before entering popup HTML.

### Configuration

All runtime config is env vars read in `pipeline/config.py` (`BEERMAP_*`, `TELEGRAM_*` for moderation notifications). Brand spelling variants are folded to canonical display names in `BRAND_ALIASES` there — add new aliases when a brand appears under multiple spellings.

## Common changes

- **New brand finder**: add `pipeline/finders/<brand>.py` subclassing `BaseFinder` (set `brand`, `url`, `serving`; implement `parse`), register in `FINDERS` in `pipeline/finders/__init__.py`, add a fixture-based test in `tests/test_finders.py`. `http_get` in `pipeline/finders/base.py` already rate-limits and sets the identifying User-Agent — external services (Overpass, Nominatim) block generic UAs, so keep using it.
- **Curating data**: edit `curation.yaml` (`action: add|remove`, `serving: fass|tank`, `verified:` date, optional `beer:` for a specific product), then re-run the pipeline.

## Autodeploy workflow

When a bugfix or feature is ready, ship it end to end without being asked for each step:

1. **Test**: `pytest -v` and `node --test web/*.test.js` — all green before anything else.
2. **Branch + commit**: create a descriptive branch off `main` (never commit to `main` directly), commit the change.
3. **PR**: push and open a GitHub PR with `gh pr create` (summary + test results in the body).
4. **Merge**: `gh pr merge --squash --delete-branch` once CI/checks (if any) pass.
5. **Deploy**: `ssh <deploy-host> "cd ~/beer-map && git pull && ./docker-run.sh"` — rebuilds the image, restarts the container, and rebuilds the dataset.
6. **Verify**: `curl -s https://zapfkompass.de/api/brands | head` and `curl -s -o /dev/null -w "%{http_code}\n" https://zapfkompass.de/` should return brands and `200`. Use the canonical domain — `beermap.jakubwaller.eu` only 301s here, so verifying against it reports a healthy deploy as a failure.

If tests fail or the deploy verification fails, stop and report — do not merge or leave the server half-deployed (re-run `./docker-run.sh` after a fix rolls forward).

## Tests

Tests are offline: finders are tested against inline HTML fixture strings (`parse()` only, no fetch), the pipeline against a stubbed `overpass_fetch`, the API via FastAPI's TestClient with tmp-path DBs. Keep it that way — no live network calls in tests.
