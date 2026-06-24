# beer-map

A map of Hamburg drinking venues, filterable by draft beer brand and serving type
(Fassbier/Tankbier). Built on a human-curated core (`curation.yaml`, highest trust),
seeded by OpenStreetMap `brewery=` tags and brand "where to drink" finders. Every
venue↔brand link records its source and last-verified date.

See `docs/specs/` (design).

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
(Code targets Python 3.9+; `from __future__ import annotations` keeps the union
type hints working on the system Python.)

## Build the dataset
```bash
python -m pipeline.run        # OSM -> finders -> curation -> web/data/venues.json
```

## Curate (this is the real work)
Edit `curation.yaml` to add/remove venue↔brand links — the highest-trust layer.
`action: add` with `serving: fass|tank` and a `verified:` date; `action: remove`
retires a stale link (e.g. a venue that switched breweries). Re-run the pipeline.

## Serve locally
```bash
python -m http.server -d web 8000   # http://localhost:8000
```

## Tests
```bash
pytest -v                  # pipeline (Python)
node --test web/*.test.js  # frontend pure functions (needs Node.js)
```

## Cron (Raspberry Pi)
```cron
0 4 * * * cd /home/pi/beer-map && /home/pi/beer-map/.venv/bin/python -m pipeline.run >> pipeline.log 2>&1
```

## Status / roadmap
- **Finders:** Ratsherrn (Fassbier) works via static HTML. The Pilsner Urquell
  Tankovna locator is behind a cookie + age gate and renders via JavaScript, so it
  yields nothing from a static fetch yet — PU/tank venues are covered by
  `curation.yaml` meanwhile. A headless fetch is part of the next phase.
- **Post-launch:** venue-menu LLM scraping as a coverage booster (a new low-trust
  source under curation).
- **Scaling to Germany:** widen `HAMBURG_QL` to a Germany area; swap
  `web/datasource.js` to a bbox/brand API — the map code is unchanged.

## API & live curation (on the Pi)
- `uvicorn api.app:app` serves the static site **and** the API on one origin.
- `POST /api/submit` — anonymous add/correct (rate-limited + honeypot).
- `/admin` — HTTP Basic (`BEERMAP_ADMIN_PW`) moderation queue; approve = instant re-export.
- Approved edits are `source="community"`, ranked just below your `manual` curation.yaml.
- **Docker deploy:** `./docker-run.sh` (build + start + build dataset), `./docker-stop.sh`.
  Full steps in `deploy/DEPLOY.md`. DNS for `beermap.jakubwaller.eu` is already live.

## Adding a brand finder
1. Save the brand's live "where to drink" page; inspect its structure.
2. Add `pipeline/finders/<brand>.py` subclassing `BaseFinder` (set `serving`).
3. Register it in `pipeline/finders/__init__.py`; add a fixture test.
