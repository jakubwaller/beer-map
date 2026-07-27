from __future__ import annotations

import os
import secrets

USER_AGENT = "beer-map/0.1 (+https://beermap.jakubwaller.eu; contact: beermap@jakubwaller.eu)"
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

# Tag values that are not a brand at all (OSM `brewery` junk words). Checked
# against the lowercased value; matching entries are dropped, not stored.
SKIP_BRANDS = {"", "yes", "no", "various", "*", "guest", "crafted"}

# Normalize known brand spellings to a canonical display name. Keys are matched
# after lowercasing and folding underscores to spaces, so one entry covers
# "Jever"/"jever" and "asahi_super_dry"/"Asahi Super Dry" alike.
BRAND_ALIASES = {
    "ratsherrn pils": "Ratsherrn",
    "ratsherrn": "Ratsherrn",
    "ratsherren": "Ratsherrn",
    "astra urtyp": "Astra",
    "astra": "Astra",
    "pilsner urquell": "Pilsner Urquell",
    "plzeňský prazdroj": "Pilsner Urquell",
    "plzensky prazdroj": "Pilsner Urquell",
    "urquell": "Pilsner Urquell",
    # In Hamburg venues "Budweiser" on tap is the Czech Budvar, so both names
    # are folded together.
    "budweiser budvar": "Budweiser Budvar",
    "budweiser": "Budweiser Budvar",
    "budvar": "Budweiser Budvar",
    "weihenstephan": "Weihenstephaner",
    "königpilsener": "König Pilsner",
    "königpilsner": "König Pilsner",
    "könig pilsener": "König Pilsner",
    "könig pilsner": "König Pilsner",
    "augustiner bräu": "Augustiner",
    "augustiner bräu münchen": "Augustiner",
}


def normalize_brand(name: str) -> str:
    """Fold a raw brand spelling to its canonical display name: underscores
    become spaces, whitespace collapses, known aliases map to their canonical
    form, and an unknown all-lowercase name gets its words capitalized (the OSM
    `brewery` tag is full of lowercase entries like "jever")."""
    name = " ".join(name.replace("_", " ").split())
    canonical = BRAND_ALIASES.get(name.lower())
    if canonical:
        return canonical
    if name.islower():
        return " ".join(w[:1].upper() + w[1:] for w in name.split())
    return name


# --- Live-curation / API settings (env-overridable for Docker deploy) ---
DB_PATH = os.environ.get("BEERMAP_DB_PATH", DB_PATH)
OUT_PATH = os.environ.get("BEERMAP_OUT_PATH", OUT_PATH)
ADMIN_USER = os.environ.get("BEERMAP_ADMIN_USER", "admin")
ADMIN_PW = os.environ.get("BEERMAP_ADMIN_PW", "")
RATE_LIMIT = int(os.environ.get("BEERMAP_RATE_LIMIT", "10"))
RATE_WINDOW_S = int(os.environ.get("BEERMAP_RATE_WINDOW_S", "3600"))
# Salt for the stored rate-limit key (a hash of the submitter's IP — the raw
# address is never written to disk). The IPv4 space is small enough to brute
# force an unsalted hash, so an unset salt falls back to a per-process random
# one: rate limiting then resets on restart, which is the safe way to fail.
IP_SALT = os.environ.get("BEERMAP_IP_SALT") or secrets.token_hex(16)
WEB_DIR = os.environ.get("BEERMAP_WEB_DIR", "web")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PUBLIC_URL = os.environ.get("BEERMAP_PUBLIC_URL", "https://beermap.jakubwaller.eu")
