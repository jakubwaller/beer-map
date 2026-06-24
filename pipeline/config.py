from __future__ import annotations

import os

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


# --- Live-curation / API settings (env-overridable for Docker deploy) ---
DB_PATH = os.environ.get("BEERMAP_DB_PATH", DB_PATH)
OUT_PATH = os.environ.get("BEERMAP_OUT_PATH", OUT_PATH)
ADMIN_USER = os.environ.get("BEERMAP_ADMIN_USER", "admin")
ADMIN_PW = os.environ.get("BEERMAP_ADMIN_PW", "")
RATE_LIMIT = int(os.environ.get("BEERMAP_RATE_LIMIT", "10"))
RATE_WINDOW_S = int(os.environ.get("BEERMAP_RATE_WINDOW_S", "3600"))
WEB_DIR = os.environ.get("BEERMAP_WEB_DIR", "web")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PUBLIC_URL = os.environ.get("BEERMAP_PUBLIC_URL", "https://beermap.jakubwaller.eu")
