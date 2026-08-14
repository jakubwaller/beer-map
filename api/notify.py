from __future__ import annotations

import smtplib
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
    httpx.post(
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


def _send_email(text: str) -> None:
    msg = EmailMessage()
    # The signed link goes in the body only: subject lines are not
    # end-to-end encrypted even between Proton mailboxes.
    msg["Subject"] = "beermap: neue Einreichung"
    msg["From"] = config.SMTP_USER
    msg["To"] = config.SMTP_TO
    msg.set_content(text)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(msg)


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    text = (f"\U0001f37a beermap: neue Einreichung — {brand} @ {venue_osm_id}\n"
            f"Pruefen: {_admin_link()}")
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            _send_telegram(text)
        except Exception:
            pass  # notifications are best-effort; never block a submission
    if config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS and config.SMTP_TO:
        try:
            _send_email(text)
        except Exception:
            pass
    return None
