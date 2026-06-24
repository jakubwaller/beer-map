from __future__ import annotations

import httpx

from .config import HAMBURG_QL, OVERPASS_URL, USER_AGENT
from .models import BrandEdge, Venue

_SKIP_BREWERY = {"", "yes", "no", "various", "*", "guest"}


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
    return [p.strip() for p in raw.split(";") if p.strip().lower() not in _SKIP_BREWERY]


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


def fetch_overpass(ql: str = HAMBURG_QL, url: str = OVERPASS_URL) -> dict:
    resp = httpx.get(url, params={"data": ql}, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    return resp.json()
