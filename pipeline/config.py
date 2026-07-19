from __future__ import annotations

import os

USER_AGENT = "beer-map/0.1 (+https://beermap.jakubwaller.eu; contact: jakub.waller@protonmail.com)"
OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Mirrors tried in order until one returns data — a single flaky/overloaded
# instance (the 406/504 we saw the main balancer hand back under load) no longer
# fails the whole build. Override the whole list with a comma-separated
# BEERMAP_OVERPASS_URLS.
OVERPASS_URLS = [u.strip() for u in os.environ.get(
    "BEERMAP_OVERPASS_URLS",
    ",".join((
        OVERPASS_URL,
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    )),
).split(",") if u.strip()]
OVERPASS_RETRIES = int(os.environ.get("BEERMAP_OVERPASS_RETRIES", "3"))
OVERPASS_BACKOFF_S = float(os.environ.get("BEERMAP_OVERPASS_BACKOFF_S", "2"))
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
    # Casing / underscore variants seen in the OSM `brewery` tag, folded to a
    # canonical display name. (Budweiser is left distinct from Budweiser Budvar
    # on purpose — they are different breweries.)
    "jever": "Jever",
    "guinness": "Guinness",
    "holsten": "Holsten",
    "einbecker": "Einbecker",
    "weihenstephaner": "Weihenstephaner",
    "weihenstephan": "Weihenstephaner",
    "königpilsener": "König Pilsner",
    "könig pilsner": "König Pilsner",
    "könig_ludwig": "König Ludwig",
    "erdinger": "Erdinger",
    "lübzer": "Lübzer",
    "kronenbourg": "Kronenbourg",
    "asahi_super_dry": "Asahi Super Dry",
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
