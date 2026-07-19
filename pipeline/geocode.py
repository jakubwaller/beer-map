from __future__ import annotations

import httpx

from .config import USER_AGENT

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_address(address: str, url: str = NOMINATIM_URL) -> tuple[float, float] | None:
    """Resolve a free-text address to (lat, lon) via Nominatim. Returns None on
    no match or a transient failure — callers should keep the venue's existing
    coordinates rather than fail the whole operation."""
    query = address if "hamburg" in address.lower() else f"{address}, Hamburg, Germany"
    try:
        resp = httpx.get(
            url,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except httpx.HTTPError:
        return None
    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None
