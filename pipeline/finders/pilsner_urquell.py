from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

_CITY = "Hamburg"


class PilsnerUrquellFinder(BaseFinder):
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
