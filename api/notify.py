from __future__ import annotations

import smtplib
from collections import Counter
from datetime import datetime
from email.message import EmailMessage

import httpx

from pipeline import config

from . import admin_links


def _admin_link() -> str:
    # A fresh signed login link per notification: opening it sets the admin
    # cookie and lands on the moderation queue without a password prompt.
    token = admin_links.make_token(datetime.now())
    if token:
        return f"{config.PUBLIC_URL}/admin/login?key={token}"
    return f"{config.PUBLIC_URL}/admin"


def _send_telegram(text: str) -> None:
    r = httpx.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            # Telegram's preview crawler fetches links server-side. The login
            # GET is side-effect-free, but there is no reason to invite it.
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    r.raise_for_status()  # a Telegram error must not count as delivered


def _send_email(text: str, subject: str) -> None:
    msg = EmailMessage()
    # The signed link goes in the body only: subject lines are not
    # end-to-end encrypted even between Proton mailboxes.
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = config.SMTP_TO
    msg.set_content(text)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(msg)


def _broadcast(text: str, subject: str) -> bool:
    """Send to every configured channel; True if at least one took it."""
    sent = False
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            _send_telegram(text)
            sent = True
        except Exception:
            pass  # notifications are best-effort; never block a submission
    if config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS and config.SMTP_TO:
        try:
            _send_email(text, subject)
            sent = True
        except Exception:
            pass
    return sent


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    text = (f"\U0001f37a beermap: neue Einreichung — {brand} @ {venue_osm_id}\n"
            f"Pruefen: {_admin_link()}")
    _broadcast(text, "beermap: neue Einreichung")
    return None


def send_partner_digest(conn, now: datetime) -> int:
    """One message for the pending partner reports no digest has announced yet.

    Partner reports are never announced one by one (a partner can send hundreds
    a day); the nightly build calls this once instead. Rows are marked only
    after a channel took the message, so a rerun the same night finds nothing,
    while a failed send or a skipped night rolls into the next digest.
    Returns the number of reports announced.
    """
    rows = conn.execute(
        "SELECT id, partner, kind FROM submissions WHERE partner IS NOT NULL "
        "AND status='pending' AND digested_at IS NULL").fetchall()
    if not rows:
        return 0
    kinds: dict[str, Counter] = {}
    for r in rows:
        kinds.setdefault(r["partner"], Counter())[r["kind"]] += 1
    lines = [f"{p}: {sum(c.values())} neue Meldung{'' if sum(c.values()) == 1 else 'en'} ("
             + ", ".join(f"{n}× {k}" for k, n in sorted(c.items())) + ")"
             for p, c in sorted(kinds.items())]
    text = ("\U0001f37a beermap: Partner-Meldungen warten in /admin\n" + "\n".join(lines)
            + f"\nPruefen: {_admin_link()}")
    if not _broadcast(text, "beermap: Partner-Meldungen"):
        return 0
    conn.executemany("UPDATE submissions SET digested_at=? WHERE id=?",
                     [(now.isoformat(timespec="seconds"), r["id"]) for r in rows])
    conn.commit()
    return len(rows)
