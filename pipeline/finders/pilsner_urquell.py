from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

_CITY = "Hamburg"


class PilsnerUrquellFinder(BaseFinder):
    """Official Pilsner Urquell Tankovna (tank-bar) locator.

    NOTE: the live page sits behind a cookie + age gate and renders its pub
    list via JavaScript, so a static fetch yields no venues yet. Tank venues
    are covered by curation.yaml meanwhile; wiring a headless fetch here is part
    of the post-launch scraping phase. The parser below is correct for the
    post-gate HTML and is exercised by a fixture test.
    """

    brand = "Pilsner Urquell"
    serving = "tank"
    url = "https://www.pilsnerurquell.com/pubs/"

    def parse(self, raw: str) -> list[FinderEntry]:
        soup = BeautifulSoup(raw, "lxml")
        seen, entries = set(), []
        for card in soup.select("li, article, .pub"):
            text = card.get_text(" ", strip=True)
            if _CITY not in text:
                continue
            name_el = card.find(["h2", "h3", "h4", "a"])
            name = name_el.get_text(strip=True) if name_el else ""
            if name and name not in seen:
                seen.add(name)
                entries.append(FinderEntry(name=name, brand=self.brand,
                                           address=_CITY, serving=self.serving))
        return entries
