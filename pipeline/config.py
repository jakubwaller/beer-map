from __future__ import annotations

USER_AGENT = "beer-map/0.1 (+https://github.com/; contact: set-me@example.com)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HAMBURG_QL = (
    '[out:json][timeout:90];'
    'area["name"="Hamburg"]["admin_level"="4"]->.a;'
    'nwr["amenity"~"^(pub|bar|biergarten|restaurant|cafe)$"](area.a);'
    'out center tags;'
)
DB_PATH = "beer-map.sqlite"
OUT_PATH = "web/data/venues.json"
CURATION_PATH = "curation.yaml"

# Normalize known brand spellings to a canonical display name.
BRAND_ALIASES = {
    "ratsherrn pils": "Ratsherrn",
    "ratsherrn": "Ratsherrn",
    "astra urtyp": "Astra",
    "astra": "Astra",
    "pilsner urquell": "Pilsner Urquell",
    "plzeňský prazdroj": "Pilsner Urquell",
    "plzensky prazdroj": "Pilsner Urquell",
    "urquell": "Pilsner Urquell",
    "budweiser budvar": "Budweiser Budvar",
    "budvar": "Budweiser Budvar",
}


def normalize_brand(name: str) -> str:
    return BRAND_ALIASES.get(name.strip().lower(), name.strip())
