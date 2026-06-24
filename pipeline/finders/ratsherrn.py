from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import FinderEntry
from .base import BaseFinder

# Heading texts on the page that are section titles, not venue names.
_SKIP = {"unsere locations", "locations", "braugasthaus"}


class RatsherrnFinder(BaseFinder):
    """Scrapes Ratsherrn's own gastro page (static HTML list of their venues).

    Addresses are not co-located with the name headings in the markup, so we
    emit the heading names and let OSM matching geo-filter them to Hamburg:
    non-Hamburg names (e.g. "Dolden Mädel Berlin") find no Hamburg venue and
    are dropped. Ratsherrn-operated venues serve Ratsherrn on Fassbier.
    """

    brand = "Ratsherrn"
    serving = "fass"
    url = "https://ratsherrn.de/ratsherrn-gastro/"

    def parse(self, raw: str) -> list[FinderEntry]:
        soup = BeautifulSoup(raw, "lxml")
        seen, entries = set(), []
        for h in soup.find_all(["h2", "h3"]):
            name = " ".join(h.get_text(" ", strip=True).split())
            key = name.lower()
            if len(name) < 4 or key in _SKIP or key in seen:
                continue
            seen.add(key)
            entries.append(FinderEntry(name=name, brand=self.brand, serving=self.serving))
        return entries
