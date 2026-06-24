# Beer-on-Tap Map — Design Spec

- **Date:** 2026-06-24
- **Status:** Approved design (pre-implementation)
- **Launch scope:** Hamburg. Architected to extend to all of Germany.

## 1. Concept

A public web map of drinking venues, each tagged with the **draft (vom Fass) beer brands** it serves. Users filter the map by brand — select "Pilsner Urquell" and every venue in view that pours it lights up.

**Differentiator:** every venue↔brand link records its **source** and **last-verified date**, so the data is trustworthy. That provenance is precisely the gap that makes Untappd's crowd-sourced data unusable for this purpose.

## 2. Goals / Non-goals

**Goals**
- Map all draft-beer-serving venues in Hamburg with the brands they pour.
- Filter the map by beer brand ("where can I drink X?").
- Show provenance + freshness for every venue↔brand link.
- Pipeline runs unattended on a Raspberry Pi (cron), matching the existing `termine` pattern.
- Architecture that scales to all of Germany without a rewrite.

**Non-goals (MVP)**
- Beer ratings / reviews / social check-ins (Untappd's noisy territory).
- Real-time accuracy for rotating craft taps (Phase 2, best-effort).
- Prices (possible later layer).
- AI phone calls to venues.

## 3. Data sources & acquisition

**Venue universe:** OpenStreetMap via Overpass (`amenity=pub|bar|biergarten|restaurant|cafe`). Free, geolocated, with name + website. Licensed ODbL.

**Tap-brand data — assembled in trust layers** (no single source is complete or current; assembling *and verifying* it is the project):
1. **Curated manual layer (`curation.yaml`) — highest trust, the MVP core.** Hand-edited venue↔brand+serving facts with a verified date; can *add or remove* links. This keeps data correct — e.g. catching that WALD switched from Pilsner Urquell to Budvar tank, or adding venues the finders miss (The Irishman, Pampa).
2. **Brand "where to drink" finders — low-trust dated seeds.** One scraper per finder: Ratsherrn (Fassbier), Pilsner Urquell Tankovna locator (Tankbier). These are **incomplete and go stale**, so they only pre-seed; curation overrides them.
3. **OSM `brewery=*` tags — low-trust seed** (~77 tagged Hamburg venues).
4. **Venue-menu LLM extraction — post-launch coverage booster** (offline LLM, never phone calls).

Trust order: **manual > finder > osm**; every link is timestamped, and the frontend shows verified links first. **Serving type:** each link records `fass` / `tank` / `unknown` so Fassbier and Tankbier are filterable; the Pilsner Urquell Tankovna locator is the seed source for tank venues.

**Matching:** finder entries are matched to OSM venues by geo proximity (~50 m) + fuzzy name match (rapidfuzz). Unmatched finder entries become standalone venue records using their own address/geo.

## 4. Data model (SQLite canonical, PostGIS-ready)

- `venues(id, osm_id, name, lat, lon, address, website, updated_at)` — manual-only venues use `osm_id = manual/<slug>`
- `brands(id, name)`
- `venue_brand(venue_id, brand_id, source, serving, confidence, first_seen, last_seen)`
  - `source ∈ {manual, finder:<brand>, osm}` (trust order); `serving ∈ {fass, tank, unknown}`

Provenance + serving + freshness are surfaced in the UI; `manual` links rank first and show a verified badge. The curated facts live in `curation.yaml` (committed to git — it *is* the dataset).

## 5. Architecture (three isolated pieces)

**A. Data pipeline (Python, Pi, cron)**
```
osm_fetch → finder scrapers → curation (apply curation.yaml, last) → SQLite → export build artifact
```
Order matters: cheap seeds first, then the curated manual layer overrides (adds/removes stale links). Each finder is a small adapter; the curation step reads `curation.yaml`.

**B. Data-access layer (the swappable seam — key to scaling)**
The frontend talks to a `DataSource` interface with exactly two queries:
- `venuesInView(bbox)` → venues (+ their brands) in the viewport
- `venuesByBrand(brandId)` → venues serving a brand

- *MVP impl:* a single static `venues.json` (GeoJSON); both queries answered client-side in memory. Ideal for Hamburg (a few thousand venues).
- *Germany impl:* a thin bbox/brand API (FastAPI over SQLite→PostGIS) answering the same two queries; optional PMTiles vector tiles for base pins. Same frontend; only this module swaps.

**C. Frontend (static site)**
MapLibre GL JS + OSM-based tiles (no Google/Mapbox bills). Brand search/filter; venue popups listing beers with source + freshness badges ("via brewery finder" / "from menu, seen 2026-06-20"). Hosted static (Cloudflare/Netlify/Pages); the Pi pushes the build artifact (and later runs the API).

## 6. Scalability to Germany

- **Pipeline & data model: unchanged.** Widen the OSM area query to Germany. Brand finders are national already, so adding cities mostly reuses the same scrapers. Hamburg is effectively a viewport on the eventual national dataset.
- **What changes: only the serving layer.** Germany is ~tens of thousands of beer venues; a single client-side JSON (tens of MB) stops being smooth at that scale. Swap the `DataSource` from static-JSON to the bbox/brand API (+ optional PMTiles pins). Because the frontend depends only on the two-query interface, this is an isolated change, not a rewrite.

## 7. Tech stack

- **Pipeline:** Python 3 (httpx, BeautifulSoup/lxml, rapidfuzz, shapely), SQLite. Phase-2 menu extraction via an LLM API (offline batch).
- **Frontend:** static HTML/JS, MapLibre GL JS, OSM-based basemap (raster for MVP, PMTiles at scale).
- **Scale serving:** FastAPI over SQLite → PostGIS.
- **Infra:** Pi runs the cron pipeline + Resend notifications; static frontend on a cheap host.

## 8. MVP scope

**Ship (curated-first):** OSM venue base + `brewery=` seeds + Ratsherrn & Pilsner Urquell finders → **curated `curation.yaml` core** → SQLite → `venues.json` → MapLibre map with brand + serving (Fass/Tank) filters and trust-ranked provenance badges.

**Defer to post-launch:** venue-menu LLM extraction (coverage booster), public submission form, Germany-wide serving API.

## 9. Legal / compliance

- Impressum (§5 DDG) + GDPR privacy policy on the site.
- "© OpenStreetMap contributors" attribution visible on/near the map.
- Scrapers respect robots.txt; throttle; identify via User-Agent.
- Store no personal data or review text.
- Brand names used factually ("serves Astra"); do not republish a finder's full database wholesale — only matched venue↔brand facts, with attribution.
- Keep pipeline-derived enrichment logically separate from raw OSM to stay clear of ODbL share-alike on the published map.

## 10. Open items to resolve during implementation

- Brand finders are incomplete/stale (the official Pilsner Urquell locator misses venues like The Irishman/Pampa and lists outdated ones like WALD) — they are seeds only; the curated layer drives coverage + accuracy. Pilsner Urquell (Tankovna) is high priority and already wired.
- Choose basemap tile provider (OSM raster vs free vector).
- Pick the static host.
