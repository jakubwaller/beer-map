from __future__ import annotations

import threading
import time

import httpx

from .config import USER_AGENT
from .osm import _address

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
_lookup_lock = threading.Lock()
_last_lookup = 0.0


def geocode_address(address: str, url: str = NOMINATIM_URL,
                    near: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Resolve a free-text address to (lat, lon) via Nominatim. Returns None on
    no match or a transient failure — callers should keep the venue's existing
    coordinates rather than fail the whole operation. `near` bounds the search
    to roughly ±25 km of that point: an address edit keeps the venue in its
    city, and a bare "Straße 5" must not jump to a same-named street elsewhere
    in Germany."""
    params = {"q": address, "format": "json", "limit": 1}
    if near is not None:
        lat, lon = near
        params["viewbox"] = f"{lon - 0.35:.4f},{lat - 0.25:.4f},{lon + 0.35:.4f},{lat + 0.25:.4f}"
        params["bounded"] = 1
    try:
        resp = httpx.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except (httpx.HTTPError, ValueError):  # ValueError: 200 with a non-JSON body
        return None
    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None


def lookup_address(osm_id: str, url: str = LOOKUP_URL) -> str | None:
    """Nominatim's address for an OSM object without addr:* tags of its own —
    it fills street and postcode from the surroundings. Returns "" when there
    is none to be had (worth caching) and None on a transient failure or while
    another lookup is in flight (worth retrying later)."""
    global _last_lookup
    kind, _, num = osm_id.partition("/")
    if kind not in ("node", "way", "relation") or not num.isdigit():
        return ""
    # One card lookup at a time, a second apart (Nominatim's policy). Never
    # wait for the slot: a burst would park every request thread of the API.
    if not _lookup_lock.acquire(blocking=False):
        return None
    try:
        time.sleep(max(0.0, _last_lookup + 1 - time.monotonic()))
        try:
            resp = httpx.get(
                url,
                params={"osm_ids": f"{kind[0].upper()}{num}", "format": "jsonv2",
                        "addressdetails": 1},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        finally:
            _last_lookup = time.monotonic()
    finally:
        _lookup_lock.release()
    try:
        a = results[0]["address"] if results else {}
        city = a.get("city") or a.get("town") or a.get("village") or a.get("municipality")
        return _address({"addr:street": a.get("road"), "addr:housenumber": a.get("house_number"),
                         "addr:postcode": a.get("postcode"), "addr:city": city}) or ""
    except (KeyError, TypeError, AttributeError):
        return ""
