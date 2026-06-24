from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

_ADDR = re.compile(r"\b\d{5}\s+[A-Za-zÄÖÜäöüß.\- ]+")


class RatsherrnFinder(BaseFinder):
    brand = "Ratsherrn"
    serving = "fass"
    url = "https://ratsherrn.de/ratsherrn-gastro/"

    def parse(self, raw: str) -> list[FinderEntry]:
        soup = BeautifulSoup(raw, "lxml")
        entries: list[FinderEntry] = []
        for heading in soup.find_all(["h2", "h3"]):
            name = heading.get_text(strip=True)
            if not name:
                continue
            block = heading.find_parent(["section", "div"]) or heading.parent
            text = block.get_text(" ", strip=True) if block else ""
            m = _ADDR.search(text)
            if not m or "Hamburg" not in m.group(0):
                continue
            entries.append(FinderEntry(name=name, brand=self.brand,
                                       address=m.group(0).strip(), serving=self.serving))
        return entries
