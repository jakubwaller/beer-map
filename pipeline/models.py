from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Venue:
    osm_id: str
    name: str
    lat: float
    lon: float
    address: str | None = None
    website: str | None = None
    # Raw OSM `opening_hours` value; parsed (and displayed verbatim when the
    # syntax is beyond the frontend parser) in web/hours.js.
    opening_hours: str | None = None


@dataclass
class BrandEdge:
    venue_osm_id: str
    brand: str
    source: str
    serving: str = "unknown"
    confidence: float = 1.0


@dataclass
class FinderEntry:
    name: str
    brand: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    website: str | None = None
    serving: str = "unknown"
