from __future__ import annotations

import httpx

from pipeline import config


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return None
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": (f"\U0001f37a beermap: neue Einreichung — {brand} @ {venue_osm_id}\n"
                         f"Pruefen: {config.PUBLIC_URL}/admin"),
            },
            timeout=10,
        )
    except Exception:
        pass  # notifications are best-effort; never block a submission
    return None
