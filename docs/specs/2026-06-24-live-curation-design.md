# Live Curation (Web Submissions + Moderation) — Design Spec

- **Date:** 2026-06-24
- **Status:** Approved design (pre-implementation)
- **Builds on:** the Hamburg beer-map MVP (`docs/specs/2026-06-24-beer-map-design.md`).

## 1. Concept

Let the maintainer **and the public** add or correct a venue's beers **live from the website**, instead of only by hand-editing `curation.yaml`. Anonymous visitors submit; the maintainer moderates; approved edits appear on the map. Everything runs on the Pi (one origin).

## 2. Goals / Non-goals

**Goals**
- A "Bier hinzufügen / melden" form on each venue so anyone can add a beer or report one as wrong/gone.
- A moderation queue: nothing public until the maintainer approves it.
- Approved edits flow into the same trust-ranked dataset and show a "geprüft" badge.
- Runs entirely on the Pi alongside the existing cron pipeline.

**Non-goals (MVP)**
- Adding brand-new venues not in OSM via the form (map-click pin = fast-follow).
- User accounts / login (submissions are anonymous).
- One-click email approval (fast-follow; MVP notifies + approves via the admin page).
- Edit history / reputation system.

## 3. Architecture (all on the Pi, one origin)

```
Browser (map + submit form)
   │  POST /api/submit         GET /api/brands, data/venues.json
   ▼
FastAPI (uvicorn) ──► SQLite (shared with pipeline, WAL mode)
   │   GET /admin (HTTP Basic): pending queue → approve / reject
   │   on approve: write source="community" edge + re-export venues.json
   ├── serves web/ static + venues.json     ← same origin, no CORS
   └── Resend: notify maintainer of new submissions
Cron pipeline (daily): OSM + curation.yaml + approved submissions → venues.json
```

FastAPI replaces `python -m http.server`: it serves the static site **and** the API, so `fetch("data/venues.json")` and `POST /api/submit` are same-origin (no CORS, one HTTPS endpoint).

## 4. Data model & trust

New table (separate from `venue_brand` so it survives pipeline rebuilds):

```
submissions(
  id, kind('add'|'remove'),
  venue_osm_id TEXT|NULL, venue_name TEXT, lat REAL|NULL, lon REAL|NULL,
  brand TEXT, serving TEXT,            -- serving in {fass, tank, unknown}
  note TEXT, submitter_ip TEXT,
  status TEXT('pending'|'approved'|'rejected'),
  created_at TEXT, decided_at TEXT|NULL
)
```

- **Pending rows are never exported.**
- On approval, the row is applied as a `venue_brand` edge with `source="community"` and re-exported immediately.
- New trust order: **`manual` (curation.yaml) > `community` (approved submissions) > `finder:*` > `osm`.** Surfaced in `fetch_venues_with_brands` SQL ordering and re-applied in `web/datasource.js`. Community edits show a "✓ geprüft" badge.

## 5. API

- `GET /api/brands` → sorted list of known brand names (form autocomplete).
- `POST /api/submit` → body `{venue_osm_id, brand, serving, kind, note, hp}`; validate + rate-limit + honeypot → insert `pending` → `{ok: true}`.
- `GET /admin` → HTTP Basic (password from env `BEERMAP_ADMIN_PW`); HTML list of pending submissions with approve/reject buttons.
- `POST /api/admin/{id}/approve` and `/reject` → HTTP Basic; update status; **approve** applies the edge + re-exports `venues.json`.

## 6. Submission flow & moderation

1. Visitor opens a venue popup → "Bier hinzufügen / melden" → picks brand (autocomplete) + Fass/Tank (+ optional note) → submit → "Danke, wird geprüft."
2. `POST /api/submit` stores a `pending` row; Resend emails the maintainer "N pending".
3. Maintainer opens `/admin`, reviews, approves/rejects.
4. **Approve** → `community` edge written + `venues.json` re-exported (instant); **reject** → row marked rejected (kept for abuse auditing).
5. The daily cron pipeline re-applies all `approved` submissions in its curation phase, so a full rebuild stays consistent.

## 7. Anti-abuse

- **Honeypot** hidden field `hp`: any value → silently drop.
- **Per-IP rate limit** (~10 submissions/hour) via a SQLite count over `submissions.created_at` + `submitter_ip` (reuses the termine IP-limiter idea).
- **Validation:** `serving ∈ {fass, tank}`, brand/note length caps, `venue_osm_id` must exist.
- HTTP Basic admin over the Pi's HTTPS; password from env, never committed.
- Store IP only for rate-limiting/abuse (documented in Datenschutz); no other personal data.

## 8. Frontend changes

- Venue popup gains a compact form: brand `<input list>` (autocomplete from `/api/brands`), Fass/Tank radio, optional note, submit button, hidden honeypot.
- Submit posts JSON to `/api/submit`; shows inline success/error.
- A minimal `/admin` page (server-rendered by FastAPI) lists pending rows with approve/reject.
- `web/datasource.js` `rank()` gains the `community` tier; popup badges render `community` as "✓ geprüft".

## 9. Pipeline integration

- New `pipeline/submissions.py`: `apply_submissions(conn, today)` reads `status='approved'` and applies each as a `source="community"` edge (add) or `delete_edges` (remove). Idempotent.
- `run_pipeline` calls it right after `apply_curation`.
- The API's approve handler reuses `apply_submissions`-style logic for the single row + `export_geojson` for instant feedback.
- `get_connection` sets `PRAGMA journal_mode=WAL` so the always-on API and the cron pipeline can share the DB file safely.

## 10. Tech stack

- **API:** FastAPI + uvicorn (Python, fits the Pi). New deps: `fastapi`, `uvicorn[standard]`, `jinja2` (admin page), `python-multipart` if form-encoded (JSON avoids it).
- **Store:** the existing SQLite DB (`DB_PATH`), WAL mode.
- **Notify:** Resend (existing account) — best-effort, failures don't block submission.
- **Serving:** uvicorn serves `web/` static + API; runs as a systemd/cron-kept service on the Pi.

## 11. MVP scope

**Ship:** submit form (add + report-wrong at existing venues) → pending queue → `/admin` approve/reject → `community` edges + instant re-export; Resend notification; anti-abuse; FastAPI serving site + API on the Pi.

**Fast-follow:** add-new-venue via map-click pin; one-click email approval links; submitter-supplied "seen on" date.

## 12. Testing

FastAPI `TestClient` + pytest: valid submit inserts pending; honeypot drop; rate-limit trips; bad serving rejected; non-existent venue rejected; approve writes `community` edge + refreshes export and the edge appears in `venues.json`; reject keeps row out of export; `apply_submissions` idempotent and respects trust order. Frontend `rank()`/badge for `community` via `node --test`.

## 13. Legal / ops

- Datenschutz: disclose that submitter IP is stored transiently for spam-prevention (Art. 6 (1)(f)).
- Admin password in env only; HTTPS required for Basic auth.
- `curation.yaml` remains the maintainer's highest-trust hand-edit path; the DB holds community submissions.

## 14. Open items

- Confirm the Pi's public HTTPS exposure method (existing reverse proxy / cert) for serving the site + admin.
- Decide rate-limit threshold and whether to also cap per-venue duplicate pending submissions.
- Service supervision on the Pi (systemd unit vs cron `@reboot`).
