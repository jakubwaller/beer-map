from __future__ import annotations

import time

import httpx

from ..config import USER_AGENT
from ..models import FinderEntry

_LAST_CALL = {"t": 0.0}
_MIN_INTERVAL = 1.0  # seconds between finder requests


def http_get(url: str) -> str:
    wait = _MIN_INTERVAL - (time.monotonic() - _LAST_CALL["t"])
    if wait > 0:
        time.sleep(wait)
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
    _LAST_CALL["t"] = time.monotonic()
    resp.raise_for_status()
    return resp.text


class BaseFinder:
    brand: str = ""
    url: str = ""
    serving: str = "unknown"

    def fetch(self) -> str:
        return http_get(self.url)

    def parse(self, raw: str) -> list[FinderEntry]:
        raise NotImplementedError

    def run(self) -> list[FinderEntry]:
        return self.parse(self.fetch())
