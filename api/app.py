from __future__ import annotations

import html
import secrets
from datetime import date, datetime
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


class Submission(BaseModel):
    venue_osm_id: str
    brand: str = ""           # empty for venue-level kinds (edit_venue/close_venue)
    serving: str = "unknown"
    beer: Optional[str] = None  # optional specific product, e.g. "Edelstoff"
    kind: str = "add"
    address: Optional[str] = None  # new address for kind="edit_venue"
    note: Optional[str] = None
    hp: Optional[str] = None  # honeypot


def _db():
    conn = get_connection(config.DB_PATH)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _require_admin(creds: HTTPBasicCredentials = Depends(_basic)):
    ok = bool(config.ADMIN_PW) and \
        secrets.compare_digest(creds.username, config.ADMIN_USER) and \
        secrets.compare_digest(creds.password, config.ADMIN_PW)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})


def create_app() -> FastAPI:
    app = FastAPI()
    # Derive the real client IP from X-Forwarded-For set by Caddy. trusted_hosts="*"
    # is safe because the container is only reachable via Caddy on the web_proxy
    # network (no public host port), so the immediate peer is always the proxy.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    @app.get("/api/brands")
    def brands(conn=Depends(_db)):
        rows = conn.execute("SELECT name FROM brands ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    @app.post("/api/submit")
    def submit(sub: Submission, request: Request, conn=Depends(_db)):
        if sub.hp:  # bot filled the honeypot
            return {"ok": True}
        payload = sub.model_dump()
        err = submissions.validate_submission(payload)
        if err:
            raise HTTPException(400, err)
        if conn.execute("SELECT 1 FROM venues WHERE osm_id=?",
                        (sub.venue_osm_id,)).fetchone() is None:
            raise HTTPException(400, "unknown venue")
        # request.client.host is set from X-Forwarded-For by ProxyHeadersMiddleware,
        # which only trusts the immediate peer (Caddy — the sole ingress). A client
        # cannot spoof it because it never connects to this app directly.
        ip = request.client.host if request.client else "unknown"
        if not submissions.within_rate_limit(conn, ip, datetime.now()):
            raise HTTPException(429, "rate limit exceeded")
        payload["venue_name"] = sub.venue_osm_id
        payload["submitter_ip"] = ip
        insert_submission(conn, payload, datetime.now().isoformat())
        notify.notify_new_submission(sub.brand or sub.kind, sub.venue_osm_id)
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin(conn=Depends(_db), _=Depends(_require_admin)):
        rows = list_submissions(conn, "pending")

        def _detail(r):
            if r["kind"] == "edit_venue":
                return "→ " + html.escape(r["address"] or "")
            if r["kind"] == "close_venue":
                return "(als geschlossen gemeldet)"
            beer = f" – {html.escape(r['beer'])}" if r.get("beer") else ""
            return f"{html.escape(r['brand'])}{beer} ({html.escape(r['serving'])})"

        items = "".join(
            f"<li>#{r['id']} <b>{html.escape(r['kind'])}</b> "
            f"{_detail(r)} @ "
            f"{html.escape(r['venue_osm_id'] or '')} "
            f"<i>{html.escape(r['note'] or '')}</i> "
            f"<button onclick=\"d({r['id']},'approve')\">approve</button> "
            f"<button onclick=\"d({r['id']},'reject')\">reject</button></li>"
            for r in rows
        ) or "<li>nothing pending</li>"
        return (
            "<!doctype html><meta charset=utf-8><title>Moderation</title>"
            "<h1>Pending submissions</h1><ul>" + items + "</ul>"
            "<script>async function d(id,a){await fetch('/api/admin/'+id+'/'+a,"
            "{method:'POST'});location.reload()}</script>"
        )

    @app.post("/api/admin/{sub_id}/approve")
    def approve(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.approve_submission(conn, sub_id, date.today().isoformat(), config.OUT_PATH):
            raise HTTPException(404, "not pending")
        return {"ok": True}

    @app.post("/api/admin/{sub_id}/reject")
    def reject(sub_id: int, conn=Depends(_db), _=Depends(_require_admin)):
        if not submissions.reject_submission(conn, sub_id, date.today().isoformat()):
            raise HTTPException(404, "not pending")
        return {"ok": True}

    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="static")
    return app


app = create_app()
