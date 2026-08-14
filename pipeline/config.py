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
_AMENITY = '"amenity"~"^(pub|bar|biergarten|restaurant|cafe)$"'
# Countries the map covers: the city sweep and the brewery-tagged layer are
# both clipped to these ISO codes.
COUNTRY_CODES = ("DE", "CZ", "AT")
# One bbox (south, west, north, east) covering Germany, Czechia and Austria.
# The nationwide sweep (pipeline/country.py) tiles it; each tile query is
# clipped to COUNTRY_CODES, so the foreign slivers inside the box only cost
# near-empty tiles, never foreign venues. The south edge sits exactly 1.0°
# (one tile row) below the pre-Austria 47.2, so the existing tile keys in
# country_tiles stay aligned.
COUNTRY_BBOX = (46.2, 5.8, 55.1, 19.0)
# Cities swept in full: every pub/bar/restaurant/cafe there becomes at least a
# gray dot (the substrate community submissions turn into data). Overpass area
# filters; Germany: admin_level 4 = Stadtstaat, 6 = kreisfreie Stadt, 8 = Stadt
# inside a Kommunalverband (Hannover sits in the level-6 "Region Hannover").
# Czechia: 8 = statutární město (obec), except Praha, which is its own kraj and
# mapped at level 4. Austria: 6 = Statutarstadt (the city doubles as its
# Bezirk), except Wien, which is its own Bundesland at level 4 — and "Salzburg"
# at level 4 is the Land, so the level filter is what picks the city. Levels
# verified against the OSM boundary relations 2026-08-07 (Mladá Boleslav
# 2026-08-11, Austria 2026-08-14).
SWEEP_AREAS = (
    '["name"="Hamburg"]["admin_level"="4"]',
    '["name"="Leipzig"]["admin_level"="6"]',
    '["name"="Berlin"]["admin_level"="4"]',
    '["name"="München"]["admin_level"="6"]',
    '["name"="Köln"]["admin_level"="6"]',
    '["name"="Frankfurt am Main"]["admin_level"="6"]',
    '["name"="Stuttgart"]["admin_level"="6"]',
    '["name"="Düsseldorf"]["admin_level"="6"]',
    '["name"="Dresden"]["admin_level"="6"]',
    '["name"="Hannover"]["admin_level"="8"]',
    '["name"="Nürnberg"]["admin_level"="6"]',
    '["name"="Bremen"]["admin_level"="6"]',
    '["name"="Praha"]["admin_level"="4"]',
    '["name"="Brno"]["admin_level"="8"]',
    '["name"="Plzeň"]["admin_level"="8"]',
    '["name"="Ostrava"]["admin_level"="8"]',
    '["name"="České Budějovice"]["admin_level"="8"]',
    '["name"="Mladá Boleslav"]["admin_level"="8"]',
    '["name"="Wien"]["admin_level"="4"]',
    '["name"="Graz"]["admin_level"="6"]',
    '["name"="Linz"]["admin_level"="6"]',
    '["name"="Salzburg"]["admin_level"="6"]',
    '["name"="Innsbruck"]["admin_level"="6"]',
    '["name"="Klagenfurt am Wörthersee"]["admin_level"="6"]',
)


def build_overpass_ql(sweep_areas=SWEEP_AREAS, country_codes=COUNTRY_CODES) -> str:
    """One query for the whole map: the sweep cities in full, plus every
    brewery-tagged venue in the covered countries (~3.9k in DE, ~1k in CZ and
    ~0.5k in AT as of 2026-08) so venues with a known brand show up nationwide. A
    country-wide sweep of *all* venue types is off the table — even counting
    them times out at 300s — until venues are served per-viewport by an API.
    The union dedupes elements both sets catch. The city sweep is additionally
    intersected with the countries area: the name+admin_level filters are not
    globally unique (e.g. Hannover, South Africa), and without the intersection
    a foreign namesake would dump its venues onto the map."""
    cities = "".join(f"area{a};" for a in sweep_areas)
    countries = "|".join(country_codes)
    return (
        '[out:json][timeout:300];'
        f'({cities})->.cities;'
        f'area["ISO3166-1"~"^({countries})$"]["admin_level"="2"]->.countries;'
        f'(nwr[{_AMENITY}](area.cities)(area.countries);'
        f'nwr[{_AMENITY}]["brewery"](area.countries););'
        'out center tags;'
    )


OVERPASS_QL = build_overpass_ql()
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
    # are folded together — and in Czechia "Budweiser" never means the US beer.
    "budweiser budvar": "Budweiser Budvar",
    "budweiser": "Budweiser Budvar",
    "budvar": "Budweiser Budvar",
    "budějovický budvar": "Budweiser Budvar",
    "budejovicky budvar": "Budweiser Budvar",
    # Czech OSM tags the brand both short and long; "Kozel" is how the taps
    # (and most of the ~70 CZ tags) spell it.
    "velkopopovický kozel": "Kozel",
    "velkopopovicky kozel": "Kozel",
    "weihenstephan": "Weihenstephaner",
    "königpilsener": "König Pilsner",
    "königpilsner": "König Pilsner",
    "könig pilsener": "König Pilsner",
    "könig pilsner": "König Pilsner",
    # Salzburg's Augustiner Bräu Kloster Mülln folds into the same chip as the
    # Munich Augustiner — the tags don't distinguish them, so neither do we.
    "augustiner bräu": "Augustiner",
    "augustiner bräu münchen": "Augustiner",
    # Austrian OSM tags the Brau-Union brand both bare and with the suffix.
    "kaiser bier": "Kaiser",
    "zillertaler": "Zillertal Bier",
    "zillertal": "Zillertal Bier",
    "guiness": "Guinness",
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
PUBLIC_URL = os.environ.get("BEERMAP_PUBLIC_URL", "https://zapfkompass.de")
# Validity window of the signed one-tap admin links in notifications.
ADMIN_LINK_TTL_S = int(os.environ.get("BEERMAP_ADMIN_LINK_TTL_S", "172800"))
# Optional SMTP notification channel (a dedicated submission token, e.g. a
# Proton SMTP token); host, user, pass and to must all be set or it stays off.
SMTP_HOST = os.environ.get("BEERMAP_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("BEERMAP_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("BEERMAP_SMTP_USER", "")
SMTP_PASS = os.environ.get("BEERMAP_SMTP_PASS", "")
SMTP_TO = os.environ.get("BEERMAP_SMTP_TO", "")
