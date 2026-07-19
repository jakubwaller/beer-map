from __future__ import annotations

import re
import time

import httpx

from .config import (HAMBURG_QL, OVERPASS_BACKOFF_S, OVERPASS_RETRIES,
                     OVERPASS_URLS, SKIP_BRANDS, USER_AGENT)
from .models import BrandEdge, Venue

# Transient responses worth retrying: rate limiting (429), gateway/overload
# (5xx), and the 406 the main balancer returns when its backends are saturated.
# Anything else (e.g. 400 for a bad query) is our fault and no mirror will fix it.
_RETRY_STATUS = {406, 429, 500, 502, 503, 504}



def _coords(el):
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def _address(tags):
    line1 = " ".join(p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p)
    line2 = " ".join(p for p in (tags.get("addr:postcode"), tags.get("addr:city")) if p)
    return ", ".join(p for p in (line1, line2) if p) or None


def _brands_from_tags(tags):
    raw = tags.get("brewery")
    if not raw:
        return []
    # ';' is the OSM list separator, but mappers use ',' too
    # ("Dithmarscher,Holsten, Flensburger" is one real tag).
    return [p.strip() for p in re.split(r"[;,]", raw)
            if p.strip().lower() not in SKIP_BRANDS]


def parse_overpass(data):
    venues, edges = [], []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        lat, lon = _coords(el)
        if lat is None:
            continue
        osm_id = f"{el['type']}/{el['id']}"
        venues.append(Venue(osm_id, name, lat, lon, _address(tags),
                            tags.get("website") or tags.get("contact:website")))
        for brand in _brands_from_tags(tags):
            edges.append(BrandEdge(osm_id, brand, "osm"))
    return venues, edges


def _fetch_once(url: str, ql: str) -> dict:
    resp = httpx.get(url, params={"data": ql}, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    return resp.json()


def fetch_overpass(ql: str = HAMBURG_QL, urls=None, retries=None, backoff=None) -> dict:
    """Fetch from Overpass, trying each mirror in turn and retrying transient
    failures (429/5xx/406, timeouts, transport errors) with exponential backoff.
    A non-transient status (e.g. 400) raises at once — mirrors won't differ. Only
    when every mirror is exhausted does the last transient error propagate."""
    urls = urls or OVERPASS_URLS
    retries = OVERPASS_RETRIES if retries is None else retries
    backoff = OVERPASS_BACKOFF_S if backoff is None else backoff
    if not urls:
        raise ValueError("no Overpass URLs configured")
    last_exc: Exception | None = None
    for url in urls:
        for attempt in range(retries):
            try:
                return _fetch_once(url, ql)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRY_STATUS:
                    raise
                last_exc = exc
            except httpx.RequestError as exc:  # timeouts, resets, DNS, etc.
                last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise last_exc  # every mirror exhausted its retries
