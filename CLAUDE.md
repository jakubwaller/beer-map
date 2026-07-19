# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A map of Hamburg drinking venues filterable by draft beer brand and serving type (Fassbier/Tankbier). A Python pipeline builds a SQLite DB + GeoJSON export; a static vanilla-JS frontend renders it; a FastAPI app adds anonymous submissions with a moderation queue. Deployed on a Raspberry Pi behind Caddy.

## Commands

```bash
source .venv/bin/activate            # venv with requirements.txt installed

pytest -v                            # Python pipeline + API tests
pytest tests/test_matching.py -v     # one file
pytest tests/test_api.py::test_submit_ok -v   # one test
node --test web/*.test.js            # frontend pure-function tests (Node)

python -m pipeline.run               # full dataset rebuild -> web/data/venues.json
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

1. **OSM**: fetch Hamburg pubs/bars via Overpass (`osm.py` — tries mirror list `OVERPASS_URLS` with retry/backoff on transient statuses), upsert venues, extract edges from `brewery=` tags.
2. **Finders**: per-brand "where to drink" scrapers (`pipeline/finders/`), fuzzy-matched to OSM venues by name + distance (`matching.py`, rapidfuzz, 85 threshold / 120 m). A failing finder logs a WARN and never kills the build.
3. **Curation**: `curation.yaml` entries applied as `source="manual"` (`curation.py`). Entries resolve a venue by `osm_id`, by `lat`+`lon` (creates a `manual/<slug>` venue), or by fuzzy name.
4. **Community**: all approved submissions re-applied (`submissions.apply_approved`) — this is why approved venue edits/closures survive the OSM re-import each build.
5. **Export**: GeoJSON to `web/data/venues.json` (`export.py`). Hidden (closed) venues stay in the DB but are excluded.

Everything is upserts keyed on `(venue_id, brand_id, source, beer)` — `beer` (specific product, `''` = brand-only) is part of the PK so one venue can list several beers of a brand. Schema migrations are hand-rolled in `pipeline/db.py` (`_MIGRATIONS` + `_migrate_venue_brand_pk`); the DB was created with `CREATE TABLE IF NOT EXISTS`, so new columns must be added there too.

### API (`api/app.py`)

`create_app()` builds a FastAPI app that also mounts `web/` as static files (single origin). `POST /api/submit` takes anonymous add/remove/edit_venue/close_venue submissions (honeypot field + per-IP rate limit); `/admin` is an HTTP-Basic (`BEERMAP_ADMIN_PW`) moderation page. Approving applies the change immediately and re-exports the GeoJSON — no pipeline run needed. `edit_venue` geocodes the new address via Nominatim (`geocode.py`) to move the pin; geocode failure falls back to text-only update. Client IP comes from `X-Forwarded-For` via ProxyHeadersMiddleware — safe only because the container has no public port and Caddy is the sole ingress.

`pipeline/export_curation.py` renders approved submissions as curation.yaml entries so they can be committed to git and survive DB loss.

### Frontend (`web/`)

No build step, no npm deps: vanilla ES modules + vendored MapLibre (`web/vendor/`). `datasource.js` holds the pure data functions (load/dedupe/filter — the only unit-tested part, via `node --test`); `app.js` does the map, filter UI, and submission forms. Venue names/addresses/brands come from OSM (publicly editable), so **every interpolated value must go through `esc()`** before entering popup HTML.

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
5. **Deploy to the Pi**: `ssh rpi "cd ~/beer-map && git pull && ./docker-run.sh"` — rebuilds the image, restarts the container, and rebuilds the dataset.
6. **Verify**: `curl -s https://beermap.jakubwaller.eu/api/brands | head` and `curl -s -o /dev/null -w "%{http_code}\n" https://beermap.jakubwaller.eu/` should return brands and `200`.

If tests fail or the deploy verification fails, stop and report — do not merge or leave the Pi half-deployed (re-run `./docker-run.sh` after a fix rolls forward).

## Tests

Tests are offline: finders are tested against inline HTML fixture strings (`parse()` only, no fetch), the pipeline against a stubbed `overpass_fetch`, the API via FastAPI's TestClient with tmp-path DBs. Keep it that way — no live network calls in tests.
