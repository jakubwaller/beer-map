from __future__ import annotations

import html
import os
import re
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from pipeline import config, submissions
from pipeline.db import get_connection, init_db, insert_submission, list_submissions

from . import notify

_basic = HTTPBasic()

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
        "description": "Kde točí pivo ze sudu a kde z tanku? Mapa ukazuje hospodu po hospodě, jaká značka se čepuje — se zdrojem a datem ověření. Osmnáct velkých měst v Německu a Česku, od Hamburku po Prahu, kompletně zmapovaných.",
        "twitter:description": "Hospoda po hospodě: jaká značka se čepuje, sud nebo tank, a odkud údaj pochází.",
        "og:image:alt": "Mapa Německa a Česka se zmapovanými výčepy",
        "og:locale": "cs_CZ",
        "og:url": "https://zapfkompass.de/?lang=cs",
    },
    "en": {
        "html_lang": "en",
        "title": "Zapfkompass – keg & tank beer",
        "og:title": "Zapfkompass — keg & tank beer on the map",
        "description": "Where do they pour keg beer, and where tank beer? The map shows pub by pub which brand is on tap — with source and verification date. Eighteen major cities in Germany and Czechia, from Hamburg to Prague, fully mapped.",
        "twitter:description": "Pub by pub: which brand is on tap, keg or tank, and where the claim comes from.",
        "og:image:alt": "Map of Germany and Czechia with the mapped taps",
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
    note: Optional[str] = None
    hp: Optional[str] = None  # honeypot


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


def _require_admin(creds: HTTPBasicCredentials = Depends(_basic)):
    ok = bool(config.ADMIN_PW) and \
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
        # request.client.host is set from X-Forwarded-For by ProxyHeadersMiddleware,
        # which only trusts the immediate peer (Caddy — the sole ingress). A client
        # cannot spoof it because it never connects to this app directly.
        ip = request.client.host if request.client else "unknown"
        # Hashed immediately: the rate limiter only needs a stable per-client
        # key, so the raw address is never persisted.
        ip_key = submissions.hash_ip(ip)
        if not submissions.within_rate_limit(conn, ip_key, datetime.now()):
            raise HTTPException(429, "rate limit exceeded")
        payload["venue_name"] = ((sub.name or "").strip()
                                 if sub.kind == "add_venue" else sub.venue_osm_id)
        payload["submitter_ip"] = ip_key
        insert_submission(conn, payload, datetime.now().isoformat())
        notify.notify_new_submission(sub.brand or sub.kind,
                                     sub.venue_osm_id or payload["venue_name"])
        return {"ok": True}

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

        def _detail(r):
            if r["kind"] == "add_venue":
                brand = (f" · {html.escape(r['brand'])} ({html.escape(r['serving'])})"
                         if r["brand"] else "")
                return html.escape(r["address"] or "") + brand
            if r["kind"] == "edit_venue":
                return "Neue Adresse: " + html.escape(r["address"] or "")
            if r["kind"] == "close_venue":
                return "Als geschlossen gemeldet"
            beer = f" – {html.escape(r['beer'])}" if r.get("beer") else ""
            return f"{html.escape(r['brand'])}{beer} ({html.escape(r['serving'])})"

        items = "".join(
            f"""<li class="card">
  <div class="head">
    <span class="kind {html.escape(r['kind'])}">{html.escape(_KIND_LABELS.get(r['kind'], r['kind']))}</span>
    <span class="venue">{html.escape(venue.get('name') or r['venue_name'] or r['venue_osm_id'] or '?')}</span>
    <span class="addr">{html.escape(venue.get('address') or '')}</span>
  </div>
  <div class="detail">{_detail(r)}</div>
  {f'<div class="note">„{html.escape(r["note"])}“</div>' if r.get('note') else ''}
  <div class="foot">
    <span class="meta">#{r['id']} · {html.escape(r['venue_osm_id'] or '')} · {html.escape((r.get('created_at') or '')[:16].replace('T', ' '))}</span>
    <span class="actions">
      <button class="approve" onclick="d({r['id']},'approve')">Freigeben</button>
      <button class="reject" onclick="d({r['id']},'reject')">Ablehnen</button>
    </span>
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:wght@500;600;700&family=Work+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
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
async function approveAll(n) {{
  if (!confirm('Alle ' + n + ' Meldungen freigeben?')) return;
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
