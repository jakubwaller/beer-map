from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import (OVERPASS_BACKOFF_S, OVERPASS_MAX_DATA_AGE_H, OVERPASS_QL,
                     OVERPASS_RETRIES, OVERPASS_SLOT_WAIT_MAX_S,
                     OVERPASS_STATUS_HOSTS, OVERPASS_URLS, SKIP_BRANDS,
                     USER_AGENT)
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
                            tags.get("website") or tags.get("contact:website"),
                            tags.get("opening_hours")))
        for brand in _brands_from_tags(tags):
            edges.append(BrandEdge(osm_id, brand, "osm"))
    return venues, edges


def slot_wait_s(url: str, get=None) -> float:
    """Seconds until this host will accept a query from our IP, per its
    /api/status page ("2 slots available now." or "Slot available after: ...,
    in 37 seconds."). Waiting for the announced moment is both quicker and
    politer than colliding with the limit and being told off with a 429. Hosts
    without that page (the mirrors) and any failure to read it answer 0 — the
    fetch then simply tries."""
    host = url.split("/")[2] if "//" in url else ""
    if host not in OVERPASS_STATUS_HOSTS:
        return 0
    try:
        resp = (get or httpx.get)(f"https://{host}/api/status",
                                  headers={"User-Agent": USER_AGENT}, timeout=15)
        text = resp.text
    except httpx.HTTPError:
        return 0
    if re.search(r"[1-9]\d* slots? available now", text):
        return 0
    waits = [int(x) for x in re.findall(r"in (\d+) seconds", text)]
    return min(min(waits) + 1, OVERPASS_SLOT_WAIT_MAX_S) if waits else 0


class StaleMirror(httpx.RequestError):
    """The mirror answered from a database older than OVERPASS_MAX_DATA_AGE_H.
    Its data is complete and well-formed — just from another season."""


class OverpassRemark(httpx.RequestError):
    """The server gave up mid-query and said so in a `remark` — the elements it
    did return are a fragment, not the answer."""


def check_fresh(data: dict, url: str, now: datetime | None = None,
                max_age_h: float | None = None) -> None:
    ts = (data.get("osm3s") or {}).get("timestamp_osm_base")
    if not ts:
        return  # not every frontend reports it; absence is not evidence
    base = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    max_age_h = OVERPASS_MAX_DATA_AGE_H if max_age_h is None else max_age_h
    age = (now or datetime.now(timezone.utc)) - base
    if age > timedelta(hours=max_age_h):
        raise StaleMirror(f"{url}: database is {age.days} days old ({ts})")


def _fetch_once(url: str, ql: str) -> dict:
    resp = httpx.get(url, params={"data": ql}, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # A query that blows its [timeout:...] budget still answers HTTP 200 — with
    # whatever elements it got around to computing and a remark admitting it.
    # Writing that through would silently hole the map.
    remark = data.get("remark") or ""
    if "timed out" in remark or "error" in remark.lower():
        raise OverpassRemark(f"overpass remark: {remark[:160]}")
    check_fresh(data, url)
    return data


def fetch_overpass(ql: str = OVERPASS_QL, urls=None, retries=None, backoff=None) -> dict:
    """Fetch from Overpass, trying each mirror in turn and retrying transient
    failures (429/5xx/406, timeouts, transport errors) with exponential backoff.
    A non-transient status (e.g. 400) raises at once — mirrors won't differ. A
    mirror answering from a stale database, or one that ran out of computation
    budget, is not retried but handed over to the next mirror. Only when every
    mirror is exhausted does the last error propagate."""
    urls = urls or OVERPASS_URLS
    retries = OVERPASS_RETRIES if retries is None else retries
    backoff = OVERPASS_BACKOFF_S if backoff is None else backoff
    if not urls:
        raise ValueError("no Overpass URLs configured")
    last_exc: Exception | None = None
    for url in urls:
        for attempt in range(retries):
            wait = slot_wait_s(url)
            if wait:
                time.sleep(wait)
            try:
                return _fetch_once(url, ql)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRY_STATUS:
                    raise
                last_exc = exc
            except (StaleMirror, OverpassRemark) as exc:
                # Neither heals in five seconds: a frozen database will not
                # thaw, and a query that exhausted the server's budget will
                # exhaust it again. A less loaded mirror may manage both.
                print(f"  WARN {exc} — skipping mirror", file=sys.stderr)
                last_exc = exc
                break
            except httpx.RequestError as exc:  # timeouts, resets, DNS, etc.
                last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
        else:
            # Without this line the log only ever shows the *last* mirror's
            # excuse, and why the main instance failed is lost.
            print(f"  WARN {url}: gave up after {retries} attempts "
                  f"({_short(last_exc)})", file=sys.stderr)
    raise last_exc  # every mirror exhausted its retries


def _short(exc: Exception | None) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc).split(" for url:")[0][:120]
