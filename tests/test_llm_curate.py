from types import SimpleNamespace

from pipeline.llm_curate import (
    VenueCuration, BrandFix, ServingSuggestion,
    load_venue_records, extract_website_text, build_user_message,
    curate_venues, render_report,
)


class _FakeMessages:
    def __init__(self, by_name):
        self.by_name = by_name
        self.calls = []

    def parse(self, *, model, max_tokens, system, messages, output_format):
        self.calls.append(messages[0]["content"])
        assert output_format is VenueCuration
        for name, sug in self.by_name.items():
            if f"Venue: {name}" in messages[0]["content"]:
                return SimpleNamespace(parsed_output=sug)
        raise AssertionError("no fake suggestion for this venue")


class _FakeClient:
    def __init__(self, by_name):
        self.messages = _FakeMessages(by_name)


FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9.99, 53.55]},
     "properties": {"osm_id": "node/1", "name": "Zum Fass", "address": "HH",
                    "website": "https://zum-fass.example",
                    "brands": [{"brand": "Astra", "source": "osm", "serving": "unknown"}]}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.0, 53.56]},
     "properties": {"osm_id": "node/2", "name": "Cafe Crumbs", "address": "HH",
                    "website": "", "brands": []}},
]}


def test_extract_website_text_strips_scripts():
    html = "<html><head><style>x{}</style></head><body><p>Wir zapfen Astra.</p>" \
           "<script>evil()</script></body></html>"
    text = extract_website_text(html)
    assert "Wir zapfen Astra." in text
    assert "evil" not in text and "x{}" not in text


def test_load_venue_records():
    recs = load_venue_records(FC)
    assert [r["osm_id"] for r in recs] == ["node/1", "node/2"]
    assert recs[0]["brands"][0]["brand"] == "Astra"


def test_build_user_message_includes_grounding_and_brands():
    msg = build_user_message(load_venue_records(FC)[0], "frisch gezapftes Astra Fassbier")
    assert "Venue: Zum Fass" in msg
    assert "Astra (serving: unknown" in msg
    assert "frisch gezapftes Astra Fassbier" in msg


def test_curate_uses_fetched_website_text_and_skips_when_no_url():
    by_name = {
        "Zum Fass": VenueCuration(
            is_beer_venue=True, relevance_reason="pub",
            brand_fixes=[], notes="",
            serving_suggestions=[ServingSuggestion(brand="Astra", serving="fass",
                                                   evidence="website: Fassbier")]),
        "Cafe Crumbs": VenueCuration(
            is_beer_venue=False, relevance_reason="bakery", brand_fixes=[],
            serving_suggestions=[], notes=""),
    }
    client = _FakeClient(by_name)
    fetched = {}

    def fake_get(url):
        fetched["url"] = url
        return "Astra Fassbier vom Fass"

    results = curate_venues(load_venue_records(FC), client, http_get=fake_get)
    # Website fetched for the venue with a URL, skipped (no call) for the empty one.
    assert fetched["url"] == "https://zum-fass.example"
    assert "Astra Fassbier vom Fass" in client.messages.calls[0]      # grounding passed in
    assert "(no website text available)" in client.messages.calls[1]  # empty website
    assert len(results) == 2


def test_render_report_emits_serving_entry_brandfix_and_relevance():
    venue = load_venue_records(FC)[0]
    sug = VenueCuration(
        is_beer_venue=True, relevance_reason="pub", notes="",
        brand_fixes=[BrandFix(from_name="Astra Urtyp", to_name="Astra")],
        serving_suggestions=[ServingSuggestion(brand="Astra", serving="fass",
                                               evidence="says Fassbier")])
    irrelevant = (load_venue_records(FC)[1],
                  VenueCuration(is_beer_venue=False, relevance_reason="bakery",
                                brand_fixes=[], serving_suggestions=[], notes=""))
    report = render_report([(venue, sug), irrelevant], today="2026-06-27")

    assert "osm_id: node/1" in report and "serving: fass" in report
    assert "action: add" in report and "verified: 2026-06-27" in report
    assert '"Astra Urtyp" -> "Astra"' in report
    assert "node/2  Cafe Crumbs: bakery" in report


def test_render_report_drops_serving_suggestion_already_known():
    # Venue already records Astra as 'fass' -> no new suggestion needed.
    venue = {"osm_id": "node/1", "name": "Zum Fass",
             "brands": [{"brand": "Astra", "serving": "fass"}]}
    sug = VenueCuration(is_beer_venue=True, relevance_reason="pub", brand_fixes=[],
                        serving_suggestions=[ServingSuggestion(brand="Astra", serving="fass",
                                                               evidence="x")], notes="")
    report = render_report([(venue, sug)], today="2026-06-27")
    assert "## Serving suggestions" in report
    section = report.split("## Serving suggestions")[1].split("##")[0]
    assert "# (none)" in section
