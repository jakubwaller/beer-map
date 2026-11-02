from __future__ import annotations

import hmac
import html
import ipaddress
import math
import os
import re
import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import (HTTPAuthorizationCredentials, HTTPBasic,
                              HTTPBasicCredentials, HTTPBearer)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from pipeline import config, dedupe, submissions
from pipeline.db import (fetch_gray_in_bbox, get_connection, init_db,
                         insert_submission, list_submissions, search_venues_db)
from pipeline.export import _feature
from pipeline.geocode import lookup_address

from . import admin_links, notify

# Cloudflare's published ranges (https://www.cloudflare.com/ips/). Update if they change.
_CLOUDFLARE_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20",
    "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)]


def client_ip(peer: str, cf_connecting_ip: str) -> str:
    """The visitor's key for rate limiting.

    `peer` is the address Caddy saw (request.client.host); behind Cloudflare
    that is a shared edge. CF-Connecting-IP is believed only when the peer is
    inside Cloudflare's ranges, otherwise a client could forge it. IPv6 is
    keyed on its /64 (one subscriber owns the whole /64, so single addresses
    would be free to rotate); IPv4-mapped IPv6 counts as the IPv4.
    shortcut: any Cloudflare zone passes the range check, not just ours; a
    spammer who routes via their own zone only dodges this soft limit
    (submissions are moderated). Upgrade: Authenticated Origin Pulls.
    """
    try:
        if any(ipaddress.ip_address(peer) in n for n in _CLOUDFLARE_NETS):
            ip = ipaddress.ip_address(cf_connecting_ip.strip())
            ip = getattr(ip, "ipv4_mapped", None) or ip
            if ip.version == 6:
                return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
            return str(ip)
    except ValueError:
        pass
    return peer


_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)
_PARTNER_PATH = "/api/partner/submit"
_ADMIN_COOKIE = "beermap_admin"

# Link-preview crawlers (WhatsApp, LinkedIn, Slack) read the static head tags
# and run no JS, so a shared ?lang= link gets its title/description localized
# server-side. "title" mirrors web/i18n.js MESSAGES["<lang>"]["title"] — keep
# them in sync; the og/description strings exist only here. German (or any
# junk value) serves index.html untouched.
_INDEX_META = {
    "cs": {
        "html_lang": "cs",
        "title": "Zapfkompass – pivo ze sudu a z tanku",
        "og:title": "Zapfkompass — pivo ze sudu a z tanku na mapě",
        "description": "Kde točí pivo ze sudu a kde z tanku? Mapa ukazuje hospodu po hospodě, jaká značka se čepuje — se zdrojem a datem ověření. Dvacet čtyři velkých měst v Německu, Rakousku a Česku, od Hamburku po Vídeň, kompletně zmapovaných.",
        "twitter:description": "Hospoda po hospodě: jaká značka se čepuje, sud nebo tank, a odkud údaj pochází.",
        "og:image:alt": "Mapa Německa, Rakouska a Česka se zmapovanými výčepy",
        "og:locale": "cs_CZ",
        "og:url": "https://zapfkompass.de/?lang=cs",
    },
    "en": {
        "html_lang": "en",
        "title": "Zapfkompass – keg & tank beer",
        "og:title": "Zapfkompass — keg & tank beer on the map",
        "description": "Where do they pour keg beer, and where tank beer? The map shows pub by pub which brand is on tap — with source and verification date. Twenty-four major cities in Germany, Austria and Czechia, from Hamburg to Vienna, fully mapped.",
        "twitter:description": "Pub by pub: which brand is on tap, keg or tank, and where the claim comes from.",
        "og:image:alt": "Map of Germany, Austria and Czechia with the mapped taps",
        "og:locale": "en_US",
        "og:url": "https://zapfkompass.de/?lang=en",
    },
}


def _localized_index(page: str, lang: Optional[str]) -> str:
    meta = _INDEX_META.get((lang or "").lower())
    if not meta:
        return page
    page = page.replace('<html lang="de">', f'<html lang="{meta["html_lang"]}">', 1)
    page = re.sub(r"<title>[^<]*</title>",
                  f"<title>{html.escape(meta['title'])}</title>", page, count=1)
    values = dict(meta, **{"og:description": meta["description"],
                           "twitter:title": meta["og:title"]})
    for attr in ("description", "og:title", "og:description", "og:locale", "og:url",
                 "og:image:alt", "twitter:title", "twitter:description"):
        page = re.sub(
            rf'((?:property|name)="{re.escape(attr)}" content=")[^"]*(")',
            lambda m, v=html.escape(values[attr]): m.group(1) + v + m.group(2),
            page, count=1)
    return page

_KIND_LABELS = {
    "add": "Neues Bier",
    "remove": "Bier entfernen",
    "edit_venue": "Adresse ändern",
    "edit_hours": "Öffnungszeiten",
    "close_venue": "Geschlossen",
    "add_venue": "Neuer Ort",
}

# Mirrors the Zapfkompass palette in web/style.css — keep the two in sync.
_ADMIN_CSS = """
:root {
  --bg: #f4ecdf;
  --card: #ffffff;
  --header: oklch(97% 0.015 80 / 0.97);
  --accent: oklch(62% 0.16 45);
  --accent-strong: oklch(48% 0.16 45);
  --ink: oklch(28% 0.02 60);
  --muted: oklch(55% 0.02 60);
  --line: oklch(88% 0.01 70);
  --good: oklch(45% 0.1 150);
  --danger: oklch(58% 0.17 40);
  --serif: 'Lora', Georgia, serif;
  --ui: 'Work Sans', system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--ui); color: var(--ink); background: var(--bg); }
header {
  display: flex; align-items: center; gap: 16px;
  padding: 12px 24px; background: var(--header);
  border-bottom: 1px solid var(--line);
  box-shadow: 0 1px 0 rgba(0, 0, 0, .06);
}
.brandmark { display: flex; align-items: baseline; gap: 8px; text-decoration: none; color: inherit; }
.brandmark .name { font-family: var(--serif); font-weight: 600; font-size: 20px; }
.brandmark .city {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 1px; color: var(--muted);
}
main { max-width: 760px; margin: 0 auto; padding: 20px 16px 60px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h1 { font-family: var(--serif); font-size: 24px; font-weight: 600; margin: 8px 0 16px; }
h1 .count {
  font-family: var(--ui); font-size: 13px; font-weight: 700; color: #fff;
  background: var(--accent); border-radius: 999px; padding: 3px 10px;
  vertical-align: 3px;
}
button {
  font: inherit; font-weight: 600; font-size: 14px;
  border: 1px solid var(--line); border-radius: 999px;
  padding: 8px 18px; cursor: pointer; background: var(--card); color: var(--ink);
}
button:hover { border-color: var(--accent); color: var(--accent-strong); }
#approve-all { background: var(--accent); border-color: var(--accent); color: #fff; }
#approve-all:hover { background: var(--accent-strong); border-color: var(--accent-strong); color: #fff; }
.subs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; box-shadow: 0 1px 0 rgba(0, 0, 0, .06);
}
.card .head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.kind {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
  border-radius: 999px; padding: 3px 10px; color: #fff; background: var(--accent);
}
.kind.remove, .kind.close_venue { background: var(--danger); }
.kind.add, .kind.add_venue { background: var(--good); }
.kind.partner { background: var(--ink); }
.venue { font-family: var(--serif); font-weight: 600; font-size: 17px; }
.addr { color: var(--muted); font-size: 13px; }
.detail { margin-top: 8px; font-size: 15px; }
.note { margin-top: 6px; font-style: italic; color: var(--muted); font-size: 14px; }
.foot {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line);
}
.meta { color: var(--muted); font-size: 12px; }
.actions { display: flex; gap: 8px; }
.approve { border-color: var(--good); color: var(--good); }
.approve:hover { background: var(--good); border-color: var(--good); color: #fff; }
.reject { border-color: var(--line); color: var(--muted); }
.reject:hover { background: var(--danger); border-color: var(--danger); color: #fff; }
.kind.dup { background: var(--danger); }
.dups { margin-top: 10px; padding: 8px 10px; border-radius: 8px; background: var(--bg); font-size: 14px; }
.dups.found { border: 1px solid var(--danger); }
.dups ul { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.dups li { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dups .src { font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--muted); }
.dups button { padding: 4px 12px; font-size: 13px; }
.dups .linked { color: var(--good); font-weight: 600; }
.empty {
  background: var(--card); border: 1px dashed var(--line); border-radius: 12px;
  padding: 32px 16px; text-align: center; color: var(--muted);
}
"""


class Submission(BaseModel):
    venue_osm_id: str = ""    # empty only for kind="add_venue" (no venue yet)
    name: Optional[str] = None  # new venue's name for kind="add_venue"
    brand: str = ""           # empty for venue-level kinds (edit_venue/close_venue)
    serving: str = "unknown"
    beer: Optional[str] = None  # optional specific product, e.g. "Edelstoff"
    kind: str = "add"
    address: Optional[str] = None  # new address for kind="edit_venue"/"add_venue"
    opening_hours: Optional[str] = None  # OSM syntax, for kind="edit_hours"
    note: Optional[str] = None
    hp: Optional[str] = None  # honeypot


class VenueHint(BaseModel):
    name: Optional[str] = None  # needed when the hint creates the venue; add_venue uses `name`
    lat: float
    lon: float


class Link(BaseModel):
    osm_id: str = ""  # one of the submission's duplicate candidates; "" unlinks


class PartnerSubmission(Submission):
    # The contract partners build against: docs/PARTNER-API.md. `hp` is ignored.
    ref: str
    venue: Optional[VenueHint] = None


def _require_partner(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> str:
    """The partner a bearer token belongs to. No PARTNER_TOKENS = nobody (401)."""
    sent = (creds.credentials if creds else "").encode()
    for name, secret in config.PARTNER_TOKENS.items():
        if hmac.compare_digest(sent, secret.encode()):
            return name
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized",
                        headers={"WWW-Authenticate": "Bearer"})


async def _json_body(request: Request):
    # Read after auth (declare it after _require_partner): a malformed body
    # without a token must answer 401, not 400.
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "malformed JSON body")


def _db():
    conn = get_connection(config.DB_PATH)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _assert_db_not_served(db_path: str, web_dir: str) -> None:
    """Refuse to start if the sqlite DB lives under the directory served as "/".

    `web_dir` is mounted with StaticFiles, so every file beneath it is
    world-downloadable — and the DB holds submission notes and rate-limit keys.
    A deploy once pointed BEERMAP_DB_PATH at web/data/; this makes that
    configuration fail loudly at boot instead of silently publishing the DB.
    """
    web_root = os.path.realpath(web_dir)
    db_dir = os.path.realpath(os.path.dirname(os.path.abspath(db_path)))
    if db_dir == web_root or db_dir.startswith(web_root + os.sep):
        raise RuntimeError(
            f"refusing to start: BEERMAP_DB_PATH ({db_path}) is inside the "
            f"publicly served BEERMAP_WEB_DIR ({web_dir}); the database would "
            "be downloadable. Point it at a directory outside web/."
        )


def _tile_bounds(z: int, x: int, y: int):
    """(south, west, north, east) of a slippy-map tile."""
    n = 2 ** z
    lon = lambda x_: x_ / n * 360.0 - 180.0                       # noqa: E731
    lat = lambda y_: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y_ / n))))  # noqa: E731
    return lat(y + 1), lon(x), lat(y), lon(x + 1)


def _from_own_site(request: Request) -> bool:
    """Whether a write came from this site's own pages.

    Cached basic credentials ride along on a cross-site form POST too, so an
    admin write must prove its origin. Fails closed: Sec-Fetch-Site when the
    browser sends it, else an Origin or Referer on PUBLIC_URL, else refused.
    Only the admin page's fetch() posts here.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site == "same-origin"
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    own = config.PUBLIC_URL.rstrip("/")
    return bool(own) and (source == own or source.startswith(own + "/"))


def _require_admin(request: Request,
                   creds: Optional[HTTPBasicCredentials] = Depends(_basic)):
    # Two ways in: the signed-link cookie set by /admin/login (SameSite=Lax,
    # so a cross-site POST never carries it) or classic basic auth.
    if request.method != "GET" and not _from_own_site(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")
    tok = request.cookies.get(_ADMIN_COOKIE)
    if tok and admin_links.verify_token(tok, datetime.now()):
        return
    ok = creds is not None and bool(config.ADMIN_PW) and \
        secrets.compare_digest(creds.username, config.ADMIN_USER) and \
        secrets.compare_digest(creds.password, config.ADMIN_PW)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})


def create_app() -> FastAPI:
    _assert_db_not_served(config.DB_PATH, config.WEB_DIR)
    app = FastAPI()
    # Derive the real client IP from X-Forwarded-For set by Caddy. trusted_hosts="*"
    # is safe because the container is only reachable via Caddy on the web_proxy
    # network (no public host port), so the immediate peer is always the proxy.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    # A dense /api/gray tile is a few hundred KB of JSON; gzip at the origin
    # spares the Pi's home uplink (Cloudflare passes origin encoding through).
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # The partner contract promises 400 for every bad body, not FastAPI's 422.
        if request.url.path == _PARTNER_PATH:
            return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=400)
        return await request_validation_exception_handler(request, exc)

    @app.get("/api/gray/{z}/{x}/{y}")
    def gray_tile(z: int, x: int, y: int, response: Response, conn=Depends(_db)):
        # Brandless venues for one slippy tile, straight off the DB — the
        # frontend fetches these per viewport (GRAY_TILE_Z in web/app.js)
        # instead of downloading a country-wide gray file it can never afford.
        if not (8 <= z <= 14 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise HTTPException(404, "tile out of range")
        south, west, north, east = _tile_bounds(z, x, y)
        rows = fetch_gray_in_bbox(conn, south, west, north, east)
        # An hour of browser caching: the data changes at most nightly, and a
        # pan back over a visited area should not re-hit the Pi.
        response.headers["Cache-Control"] = "public, max-age=3600"
        return {"type": "FeatureCollection",
                "features": [_feature(r, with_brands=False) for r in rows]}

    @app.get("/api/search")
    def search_endpoint(q: str = "", conn=Depends(_db)):
        # Nationwide venue search over the folded search_key column. The
        # client merges these into its locally loaded pool and re-ranks, so
        # the response is the same FeatureCollection shape as the exports —
        # brands included, because a hit may not be loaded client-side yet.
        rows = search_venues_db(conn, q, limit=30)
        return {"type": "FeatureCollection",
                "features": [_feature(r, with_brands=True) for r in rows]}

    @app.get("/api/address/{kind}/{num}")
    def address(kind: str, num: str, response: Response, conn=Depends(_db)):
        # The card's address for a venue OSM tags none on. Only visible venues
        # are looked up, so this is no open proxy to Nominatim, and every
        # answer is kept, so each venue costs Nominatim one request ever.
        osm_id = f"{kind}/{num}"
        row = conn.execute("SELECT address FROM venues WHERE osm_id=? AND COALESCE(hidden, 0)=0",
                           (osm_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown venue")
        found = row["address"]
        if not found:
            hit = conn.execute("SELECT address FROM looked_up_addresses WHERE osm_id=?",
                               (osm_id,)).fetchone()
            if hit:
                found = hit["address"]
            else:
                found = lookup_address(osm_id)
                if found is None:  # transient: answer empty, cache nothing
                    return {"address": None}
                conn.execute("INSERT OR REPLACE INTO looked_up_addresses VALUES (?, ?, ?)",
                             (osm_id, found, date.today().isoformat()))
                conn.commit()
        response.headers["Cache-Control"] = "public, max-age=86400"
        return {"address": found or None}

    @app.get("/api/brands")
    def brands(conn=Depends(_db)):
        # Only brands that are actually poured somewhere on the map — orphaned
        # brand rows and brands only on hidden (closed) venues stay out.
        rows = conn.execute(
            """
            SELECT DISTINCT b.name FROM brands b
            JOIN venue_brand vb ON vb.brand_id = b.id
            JOIN venues v ON v.id = vb.venue_id
            WHERE COALESCE(v.hidden, 0) = 0
            ORDER BY b.name
            """
        ).fetchall()
        return [r["name"] for r in rows]

    @app.post("/api/submit")
    def submit(sub: Submission, request: Request, conn=Depends(_db)):
        if sub.hp:  # bot filled the honeypot
            return {"ok": True}
        payload = sub.model_dump()
        err = submissions.validate_submission(payload)
        if err:
            raise HTTPException(400, err)
        if sub.kind != "add_venue" and conn.execute(
                "SELECT 1 FROM venues WHERE osm_id=?",
                (sub.venue_osm_id,)).fetchone() is None:
            raise HTTPException(400, "unknown venue")
        # request.client.host is the peer Caddy saw (via ProxyHeadersMiddleware);
        # client_ip() swaps in the visitor when that peer is a Cloudflare edge.
        peer = request.client.host if request.client else "unknown"
        ip = client_ip(peer, request.headers.get("cf-connecting-ip", ""))
        # Hashed immediately: the rate limiter only needs a stable per-client
        # key, so the raw address is never persisted.
        ip_key = submissions.hash_ip(ip)
        if not submissions.within_rate_limit(conn, ip_key, datetime.now()):
            raise HTTPException(429, "rate limit exceeded")
        payload["venue_name"] = ((sub.name or "").strip()
                                 if sub.kind == "add_venue" else sub.venue_osm_id)
        payload["submitter_ip"] = ip_key
        if sub.kind == "add_venue":
            payload["venue_osm_id"] = ""  # apply_one trusts this column; assigned at approval
        insert_submission(conn, payload, datetime.now().isoformat())
        notify.notify_new_submission(sub.brand or sub.kind,
                                     sub.venue_osm_id or payload["venue_name"])
        return {"ok": True}

    @app.post(_PARTNER_PATH)
    def partner_submit(partner: str = Depends(_require_partner),
                       body: dict = Depends(_json_body), conn=Depends(_db)):
        try:
            sub = PartnerSubmission.model_validate(body)
        except ValidationError as e:
            raise RequestValidationError(e.errors())
        # (partner, ref) is unique, so a retry of a stored report changes nothing.
        if submissions.partner_ref_exists(conn, partner, sub.ref):
            return {"ok": True, "duplicate": True}
        payload = sub.model_dump()
        payload["address"] = (sub.address or "").strip() or None
        known = sub.kind != "add_venue" and conn.execute(
            "SELECT 1 FROM venues WHERE osm_id=?", (sub.venue_osm_id,)).fetchone() is not None
        err = submissions.validate_partner_submission(payload, known)
        if err:
            raise HTTPException(400, err)
        if not submissions.within_partner_cap(conn, partner, datetime.now()):
            raise HTTPException(429, "partner daily cap exceeded")
        hint = payload.pop("venue") or {}
        # One server speaks for many players: no IP, and partners send no user data.
        payload.update(partner=partner, submitter_ip=None, venue_name=sub.venue_osm_id)
        if sub.kind == "add_venue":
            payload.update(venue_osm_id="", venue_name=sub.name.strip(),
                           lat=hint["lat"], lon=hint["lon"])
        else:
            if sub.kind == "edit_venue":
                payload["new_name"] = (sub.name or "").strip() or None
            if not known:  # approval creates the venue from the hint
                payload.update(venue_name=hint["name"].strip(),
                               hint_lat=hint["lat"], hint_lon=hint["lon"])
        try:
            insert_submission(conn, payload, datetime.now().isoformat())
        except sqlite3.IntegrityError:  # a concurrent retry of the same ref got there first
            return {"ok": True, "duplicate": True}
        # No notification here: the nightly build sends one digest (notify.send_partner_digest).
        return {"ok": True}

    @app.get("/admin/login")
    def admin_login(key: str = ""):
        # One-tap login from the submission notification. The GET only
        # authenticates — link-preview crawlers may fetch it, so every
        # mutation stays behind a POST on the /admin page.
        now = datetime.now()
        if not admin_links.verify_token(key, now):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Link ungültig oder abgelaufen — /admin geht weiter per Basic Auth")
        resp = RedirectResponse("/admin", status_code=302)
        resp.set_cookie(
            _ADMIN_COOKIE, key,
            max_age=max(0, admin_links.token_expiry(key) - int(now.timestamp())),
            httponly=True, secure=True, samesite="lax")
        return resp

    @app.get("/admin", response_class=HTMLResponse)
    def admin(conn=Depends(_db), _=Depends(_require_admin)):
        rows = list_submissions(conn, "pending")

        venues: dict[str, dict] = {}
        osm_ids = sorted({r["venue_osm_id"] for r in rows if r["venue_osm_id"]})
        if osm_ids:
            marks = ",".join("?" for _ in osm_ids)
            venues = {
                v["osm_id"]: dict(v) for v in conn.execute(
                    f"SELECT osm_id, name, address FROM venues WHERE osm_id IN ({marks})",
                    osm_ids,
                )
            }

        # Duplicate candidates for every add_venue. The network lookups are
        # capped per render (dedupe.Budget); answers are cached on the rows,
        # so each reload ("Prüfen") fills in a few more.
        dups: dict[int, tuple[list[dict], bool | None]] = {}
        budget = dedupe.Budget()
        for r in rows:
            if r["kind"] == "add_venue":
                dups[r["id"]] = dedupe.candidates(conn, r, budget)
        check = '<button onclick="location.reload()">Prüfen</button>'

        def _dups(r):
            if r["id"] not in dups:
                return ""
            cands, osm_ok = dups[r["id"]]
            if dedupe.origin(r) is None:
                if r.get("geocode_miss"):
                    return '<div class="dups">Adresse nicht gefunden – Duplikatprüfung nicht möglich.</div>'
                if r.get("geocode_error"):
                    return f'<div class="dups">Geocoding gerade nicht erreichbar – Prüfen später. {check}</div>'
                return f'<div class="dups">Kandidaten werden geladen. {check}</div>'
            linked = r.get("assigned_osm_id")
            lines = []
            for c in cands:
                oid = html.escape(c["osm_id"])
                label = html.escape(c["name"] or "(ohne Name)")
                kind = f" · {html.escape(c['kind'])}" if c["kind"] else ""
                ref = (f'<a href="https://www.openstreetmap.org/{oid}" target="_blank" rel="noopener">{oid}</a>'
                       if c["osm_id"].split("/")[0] in ("node", "way", "relation") else oid)
                act = (f'<span class="linked">verknüpft</span> <button data-osm="" onclick="link({r["id"]},this.dataset.osm)">Lösen</button>'
                       if c["osm_id"] == linked else
                       f'<button data-osm="{oid}" onclick="link({r["id"]},this.dataset.osm)">Verknüpfen</button>')
                src = "Zapfkompass" if c["source"] == "db" else "OSM"
                lines.append(f'<li><span class="src">{src}</span> <b>{label}</b>{kind} · {c["dist"]} m · {ref} {act}</li>')
            status_line = {False: " OSM nicht erreichbar – nur Zapfkompass geprüft.",
                           None: f" OSM noch nicht geprüft. {check}"}.get(osm_ok, "")
            if not lines:
                return f'<div class="dups">Keine Duplikate gefunden.{status_line}</div>'
            return (f'<div class="dups found">Mögliche Duplikate:{status_line}'
                    f'<ul>{"".join(lines)}</ul></div>')

        def _buttons(r):
            approve = "Freigeben"
            if r["kind"] == "add_venue" and r.get("assigned_osm_id"):
                approve = "Freigeben → " + html.escape(r["assigned_osm_id"])
            elif dups.get(r["id"], ([],))[0]:
                approve = "Als neu freigeben"
            def btn(cls, action, label):
                return f"""<button class="{cls}" onclick="d({r['id']},'{action}')">{label}</button>"""
            out = btn("approve", "approve", approve)
            if dups.get(r["id"], ([],))[0]:
                out += btn("reject", "reject", "Duplikat – ablehnen")
            return out + btn("reject", "reject", "Ablehnen")

        def _dup_flag(r):
            cands = dups.get(r["id"], ([],))[0]
            if r.get("assigned_osm_id") or not any(c["source"] == "db" for c in cands):
                return ""
            return '<span class="kind dup">Duplikat?</span>'

        def _coords(lat, lon):
            return f"{lat:.5f}, {lon:.5f}"

        def _place(r, venue):
            # A partner report on a venue we lack: say approval will create it.
            if not venue and r.get("hint_lat") is not None and r["kind"] != "add_venue":
                return "Neu anlegen bei " + _coords(r["hint_lat"], r["hint_lon"])
            return venue.get("address") or ""

        def _detail(r):
            if r["kind"] == "add_venue":
                brand = (f" · {html.escape(r['brand'])} ({html.escape(r['serving'])})"
                         if r["brand"] else "")
                where = r["address"] or (_coords(r["lat"], r["lon"]) if r["lat"] is not None else "")
                return html.escape(where) + brand
            if r["kind"] == "edit_venue":
                parts = [f"Neuer Name: {html.escape(r['new_name'])}"] if r.get("new_name") else []
                if r["address"]:
                    parts.append("Neue Adresse: " + html.escape(r["address"]))
                return " · ".join(parts)
            if r["kind"] == "edit_hours":
                return "Neue Zeiten: " + html.escape(r["opening_hours"] or "")
            if r["kind"] == "close_venue":
                return "Als geschlossen gemeldet"
            beer = f" – {html.escape(r['beer'])}" if r.get("beer") else ""
            return f"{html.escape(r['brand'])}{beer} ({html.escape(r['serving'])})"

        items = "".join(
            f"""<li class="card">
  <div class="head">
    <span class="kind {html.escape(r['kind'])}">{html.escape(_KIND_LABELS.get(r['kind'], r['kind']))}</span>
    {f'<span class="kind partner">{html.escape(r["partner"])}</span>' if r.get('partner') else ''}
    {_dup_flag(r)}
    <span class="venue">{html.escape(venue.get('name') or r['venue_name'] or r['venue_osm_id'] or '?')}</span>
    <span class="addr">{html.escape(_place(r, venue))}</span>
  </div>
  <div class="detail">{_detail(r)}</div>
  {f'<div class="note">„{html.escape(r["note"])}“</div>' if r.get('note') else ''}
  {_dups(r)}
  <div class="foot">
    <span class="meta">#{r['id']} · {html.escape(r['venue_osm_id'] or '')} · {html.escape((r.get('created_at') or '')[:16].replace('T', ' '))}</span>
    <span class="actions">{_buttons(r)}</span>
  </div>
</li>"""
            for r in rows
            for venue in [venues.get(r["venue_osm_id"] or "", {})]
        ) or '<li class="empty">Nichts offen – alles erledigt. 🍺</li>'

        approve_all_btn = (
            f'<button id="approve-all" onclick="approveAll({len(rows)})">'
            f"Alle {len(rows)} freigeben</button>" if rows else ""
        )
        return f"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Zapfkompass – Moderation</title>
<link href="/fonts/fonts.css" rel="stylesheet">
<style>{_ADMIN_CSS}</style>
</head><body>
<header>
  <a class="brandmark" href="/"><span class="name">Zapfkompass</span><span class="city">Moderation</span></a>
</header>
<main>
  <div class="toolbar">
    <h1>Offene Meldungen <span class="count">{len(rows)}</span></h1>
    {approve_all_btn}
  </div>
  <ul class="subs">{items}</ul>
</main>
<script>
async function d(id, a) {{
  await fetch('/api/admin/' + id + '/' + a, {{method: 'POST'}});
  location.reload();
}}
async function link(id, osm) {{
  const r = await fetch('/api/admin/' + id + '/link', {{method: 'POST',
    headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{osm_id: osm}})}});
  if (!r.ok) alert('Verknüpfen fehlgeschlagen');
  location.reload();
}}
async function approveAll(n) {{
  if (!confirm('Alle ' + n + ' Meldungen freigeben? (Neue Orte mit Duplikat-Verdacht bleiben offen.)')) return;
  await fetch('/api/admin/approve-all', {{method: 'POST'}});
  location.reload();
}}
</script>
</body></html>"""

    @app.post("/api/admin/approve-all")
    def approve_all(conn=Depends(_db), _=Depends(_require_admin)):
        n = submissions.approve_all_pending(
            conn, date.today().isoformat(), config.OUT_PATH)
        return {"ok": True, "approved": n}

    @app.post("/api/admin/{sub_id}/approve")
    def approve(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.approve_submission(conn, sub_id, date.today().isoformat(), config.OUT_PATH):
            raise HTTPException(404, "not pending, or could not be applied "
                                     "(venue gone / address not geocodable)")
        return {"ok": True}

    @app.post("/api/admin/{sub_id}/link")
    def link(sub_id: int, body: Link, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.link_submission(conn, sub_id, body.osm_id.strip()):
            raise HTTPException(404, "not a pending add_venue, or not one of its candidates")
        return {"ok": True}

    @app.post("/api/admin/{sub_id}/reject")
    def reject(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.reject_submission(conn, sub_id, date.today().isoformat()):
            raise HTTPException(404, "not pending")
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(lang: Optional[str] = None):
        page = (Path(config.WEB_DIR) / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(_localized_index(page, lang))

    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="static")
    return app


app = create_app()
