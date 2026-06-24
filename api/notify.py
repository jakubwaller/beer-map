from __future__ import annotations

import httpx

from pipeline import config


def notify_new_submission(brand: str, venue_osm_id: str) -> None:
    if not (config.RESEND_API_KEY and config.NOTIFY_TO and config.NOTIFY_FROM):
        return None
    try:
        httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={
                "from": config.NOTIFY_FROM,
                "to": [config.NOTIFY_TO],
                "subject": "beermap: neue Einreichung",
                "text": f"Neue Einreichung: {brand} @ {venue_osm_id}\nPruefen: /admin",
            },
            timeout=10,
        )
    except Exception:
        pass  # notifications are best-effort; never block a submission
    return None
