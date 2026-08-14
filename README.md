# beer-map

**Live at [zapfkompass.de](https://zapfkompass.de)** — the site is called *Zapfkompass*;
the repo, Docker service and env files stay `beermap`.

A map of German, Austrian and Czech drinking venues, filterable by draft beer
brand and serving type (Fassbier/Tankbier). Every pub, bar, restaurant, café and
Biergarten in all three countries appears as at least a clickable gray dot — the
nationwide sweep (`pipeline/country.py`) ingests them all, and the frontend
loads the brandless majority per viewport from `/api/gray` instead of shipping
a country-sized file. Venues with a known brand (OSM `brewery=` tag, curation,
finders) load up front nationwide. The famous Czech tankovnas (the Lokál chain,
U Pinkasů, Na Parkánu, Budvarka …) are seeded as curated tank entries — OSM has
no tag for tank beer. Built on a human-curated core (`curation.yaml`,
highest trust), seeded by OpenStreetMap and brand "where to drink" finders. Every
venue↔brand link records its source and last-verified date. Venues also show their
OSM opening hours ("Jetzt geöffnet · bis 22:00"), and the search ranks matches
across name, brand, beer and address.

See `docs/specs/` (design).

## Licence

The **code** is MIT (see `LICENSE`).

The **data** is a different matter: venues, coordinates and addresses are derived
from [OpenStreetMap](https://www.openstreetmap.org/copyright), so `curation.yaml`
and the generated `web/data/venues.json` are © OpenStreetMap contributors and
licensed under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Reuse them under those terms — keep the attribution, and share derived databases
alike. Brand↔venue links added by hand or by community submission are published
under the same licence.

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
python -m pipeline.country    # nationwide venue sweep, tile by tile (~145 requests)
```
The nightly `pipeline.run` refreshes the sweep cities, brewery-tagged venues,
finders and curation; `pipeline.country` fills the DB with every venue in DE+CZ+AT
(the gray-dot substrate served by `/api/gray`) and is meant to run weekly —
`--resume` continues an interrupted sweep.

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
0 2 * * 0 cd /home/pi/beer-map && /home/pi/beer-map/.venv/bin/python -m pipeline.country >> country.log 2>&1
```

## Status / roadmap
- **Finders:** Ratsherrn (Fassbier) works via static HTML. The Pilsner Urquell
  Tankovna locator is behind a cookie + age gate and renders via JavaScript, so it
  yields nothing from a static fetch yet — PU/tank venues (including the Czech
  tankovnas) are covered by `curation.yaml` meanwhile. A headless fetch is part
  of the next phase.
- **Post-launch:** venue-menu LLM scraping as a coverage booster (a new low-trust
  source under curation).
- **Scaling beyond the sweep cities: done.** A single country-wide Overpass
  query times out (~250k elements in Germany alone), so `pipeline/country.py`
  sweeps a grid of bbox tiles instead and the frontend fetches the brandless
  venues per viewport from `/api/gray/{z}/{x}/{y}` (gray dots render from
  zoom 10). `/api/search` extends the search box to the whole database, so a
  village pub is findable before its area was ever panned over. The
  `SWEEP_AREAS` city list still controls what the *nightly* refresh re-imports.

## API & live curation (on the Pi)
- `uvicorn api.app:app` serves the static site **and** the API on one origin.
- `GET /api/gray/{z}/{x}/{y}` — brandless venues of one slippy tile (z 8–14),
  straight off the DB; `GET /api/search?q=` — nationwide folded name/address
  search.
- `POST /api/submit` — anonymous add/correct (rate-limited + honeypot).
- `/admin` — HTTP Basic (`BEERMAP_ADMIN_PW`) moderation queue; approve = instant re-export.
- Approved edits are `source="community"`, ranked just below your `manual` curation.yaml.
- **Docker deploy:** `./docker-run.sh` (build + start + build dataset), `./docker-stop.sh`.
  Full steps in `deploy/DEPLOY.md`. `zapfkompass.de` is live; `www.zapfkompass.de` and the
  old `beermap.jakubwaller.eu` redirect there.

## Adding a brand finder
1. Save the brand's live "where to drink" page; inspect its structure.
2. Add `pipeline/finders/<brand>.py` subclassing `BaseFinder` (set `serving`).
3. Register it in `pipeline/finders/__init__.py`; add a fixture test.
