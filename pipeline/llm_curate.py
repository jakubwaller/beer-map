"""Use Claude to *suggest* curation, grounded in real sources — never invent data.

This is an OFFLINE REVIEW tool: it reads the venues, optionally fetches each
venue's own website for grounding, asks Claude to (1) normalise brand names,
(2) judge whether the place is actually a beer-serving pub/bar, and (3) suggest
a serving type (Fass/Tank) *only* when the website gives evidence — then writes
a human-readable report. Nothing is applied automatically: you review the
report and copy the parts you trust into curation.yaml (or BRAND_ALIASES).

Why grounded-only: an LLM does not know real tap lists and would hallucinate
them, which would poison a trust-graded dataset. The only sources it is allowed
to use are the OSM tags already recorded and the venue's website text. Google
Maps photos/reviews are NOT used — there is no API access here and scraping
Google violates its terms; add a Places API integration later if wanted.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python -m pipeline.llm_curate --venues web/data/venues.json --limit 20
    python -m pipeline.llm_curate --url https://beermap.jakubwaller.eu/data/venues.json -o report.md

The data file is gitignored, so pull it from the live site with --url, or point
--venues at a local export.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Callable, Literal, Optional

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from .config import OUT_PATH

MODEL = "claude-opus-4-8"
_WEBSITE_CHARS = 4000  # cap website text fed to the model

SYSTEM_PROMPT = (
    "You are a meticulous data curator for a map of Hamburg pubs that serve "
    "draught beer (Fass = keg, Tank = unpasteurised tank beer).\n"
    "You will be given one venue: its OpenStreetMap tags, the beers already "
    "recorded for it, and — when available — text scraped from the venue's own "
    "website.\n\n"
    "Rules, in order of importance:\n"
    "1. GROUND EVERYTHING. Only use the OSM tags and the website text provided. "
    "You do NOT know real-world tap lists — never invent a brand or a serving "
    "type. If the evidence is not in the input, say so and leave it 'unknown'.\n"
    "2. Brand name fixes: suggest a normalisation only for an obvious spelling/"
    "formatting variant of a beer brand already recorded (e.g. 'Astra Urtyp' -> "
    "'Astra'). Do not add brands.\n"
    "3. Serving type: suggest 'fass' or 'tank' for an already-recorded brand "
    "ONLY if the website text explicitly indicates it (quote the evidence). "
    "Otherwise 'unknown'.\n"
    "4. Relevance: judge whether this is genuinely a place serving draught beer "
    "(vs a cafe, bakery, fast-food, etc.). Be conservative; explain briefly.\n"
    "Keep every suggestion conservative and evidence-based."
)


# --- Structured output schema (validated by client.messages.parse) ---
class BrandFix(BaseModel):
    from_name: str
    to_name: str


class ServingSuggestion(BaseModel):
    brand: str
    serving: Literal["fass", "tank", "unknown"]
    evidence: str


class VenueCuration(BaseModel):
    is_beer_venue: bool
    relevance_reason: str
    brand_fixes: list[BrandFix]
    serving_suggestions: list[ServingSuggestion]
    notes: str


def load_venue_records(fc: dict) -> list[dict]:
    out = []
    for f in fc.get("features", []):
        p = f.get("properties", {})
        out.append({
            "osm_id": p.get("osm_id") or "",
            "name": p.get("name") or "",
            "address": p.get("address") or "",
            "website": p.get("website") or "",
            "brands": p.get("brands") or [],
        })
    return out


def extract_website_text(html: str) -> str:
    """Visible text from a page, scripts/styles stripped, collapsed and capped."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:_WEBSITE_CHARS]


def fetch_website_text(url: str, timeout: float = 15.0) -> str:
    """Best-effort fetch + extract; returns '' on any failure (offline-friendly)."""
    if not url:
        return ""
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout,
                         headers={"User-Agent": "beer-map-curator/0.1"})
        resp.raise_for_status()
        return extract_website_text(resp.text)
    except Exception:
        return ""


def build_user_message(venue: dict, website_text: str) -> str:
    brands = venue.get("brands") or []
    if brands:
        lines = "\n".join(
            f"- {b.get('brand', '')} (serving: {b.get('serving', 'unknown')}, "
            f"source: {b.get('source', '')})" for b in brands)
    else:
        lines = "- (none recorded)"
    site = website_text.strip() or "(no website text available)"
    return (
        f"Venue: {venue.get('name', '')}\n"
        f"Address: {venue.get('address') or 'unknown'}\n"
        f"OSM id: {venue.get('osm_id', '')}\n"
        f"Website: {venue.get('website') or 'none'}\n\n"
        f"Beers already recorded:\n{lines}\n\n"
        f"Website text (grounding source — may be empty):\n\"\"\"\n{site}\n\"\"\""
    )


def suggest_for_venue(client, venue: dict, website_text: str,
                      model: str = MODEL) -> VenueCuration:
    resp = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],  # cached across venues
        messages=[{"role": "user", "content": build_user_message(venue, website_text)}],
        output_format=VenueCuration,
    )
    return resp.parsed_output


def curate_venues(records: list[dict], client, model: str = MODEL,
                  fetch_web: bool = True,
                  http_get: Callable[[str], str] = fetch_website_text,
                  limit: Optional[int] = None) -> list[tuple[dict, VenueCuration]]:
    selected = records[:limit] if limit else records
    out = []
    for r in selected:
        text = http_get(r["website"]) if (fetch_web and r.get("website")) else ""
        out.append((r, suggest_for_venue(client, r, text, model)))
    return out


def _current_serving(venue: dict, brand: str) -> str:
    for b in venue.get("brands") or []:
        if b.get("brand") == brand:
            return b.get("serving", "unknown")
    return "unknown"


def render_report(results: list[tuple[dict, VenueCuration]],
                  today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    serving_blocks, brand_fixes, relevance_flags = [], [], []

    for venue, sug in results:
        osm_id, name = venue.get("osm_id", ""), venue.get("name", "")
        for s in sug.serving_suggestions:
            # Only worth surfacing if it adds a known serving we don't already have.
            if s.serving in ("fass", "tank") and _current_serving(venue, s.brand) != s.serving:
                serving_blocks.append(
                    f"- osm_id: {osm_id}\n"
                    f"  brand: {s.brand}\n"
                    f"  serving: {s.serving}\n"
                    f"  action: add\n"
                    f"  verified: {today}\n"
                    f"  note: \"LLM-Vorschlag (Website); prüfen\"\n"
                    f"  # {name}: {s.evidence.strip()}")
        for fx in sug.brand_fixes:
            if fx.from_name.strip().lower() != fx.to_name.strip().lower():
                brand_fixes.append(f"- \"{fx.from_name}\" -> \"{fx.to_name}\"   # {name}")
        if not sug.is_beer_venue:
            relevance_flags.append(f"- {osm_id}  {name}: {sug.relevance_reason.strip()}")

    parts = [f"# LLM curation suggestions — {today}",
             "# REVIEW before using. Grounded in OSM tags + venue websites only.", ""]

    parts.append("## Serving suggestions (review, then paste into curation.yaml)")
    parts.append("\n".join(serving_blocks) if serving_blocks else "# (none)")
    parts.append("")
    parts.append("## Brand-name normalisations (consider adding to BRAND_ALIASES)")
    parts.append("\n".join(sorted(set(brand_fixes))) if brand_fixes else "# (none)")
    parts.append("")
    parts.append("## Possibly-not-a-beer-venue (manual review)")
    parts.append("\n".join(relevance_flags) if relevance_flags else "# (none)")
    parts.append("")
    return "\n".join(parts)


def _load_fc(args) -> dict:
    if args.url:
        resp = httpx.get(args.url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        return resp.json()
    with open(args.venues, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--venues", default=OUT_PATH, help="path to venues.json")
    src.add_argument("--url", help="fetch venues.json from this URL instead")
    ap.add_argument("--limit", type=int, help="only process the first N venues")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--no-web", action="store_true", help="skip website fetching")
    ap.add_argument("-o", "--out", help="write report here (default: stdout)")
    args = ap.parse_args(argv)

    try:
        import anthropic
    except ImportError:
        print("This tool needs the anthropic SDK: pip install 'anthropic>=0.69.0'",
              file=sys.stderr)
        return 2

    records = load_venue_records(_load_fc(args))
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    results = curate_venues(records, client, model=args.model,
                            fetch_web=not args.no_web, limit=args.limit)
    report = render_report(results)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"wrote {args.out} ({len(results)} venues)", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
