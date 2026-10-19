import httpx

from pipeline import geocode
from pipeline.geocode import geocode_address


def _fake_get(payload):
    """Stand-in for httpx.get returning `payload` as JSON and recording params."""
    seen = {}

    def _get(url, params=None, headers=None, timeout=None):
        seen.update(params)
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    return _get, seen


def test_geocode_returns_first_hit(monkeypatch):
    get, seen = _fake_get([{"lat": "53.51", "lon": "10.01"}])
    monkeypatch.setattr(geocode.httpx, "get", get)
    assert geocode_address("Neue Allee 7, Hamburg") == (53.51, 10.01)
    assert "viewbox" not in seen  # unbounded without a `near` point


def test_geocode_near_bounds_the_search(monkeypatch):
    # An address edit must stay near the venue's current pin — a bare street
    # name must not resolve to a same-named street elsewhere in Germany.
    get, seen = _fake_get([{"lat": "51.35", "lon": "12.38"}])
    monkeypatch.setattr(geocode.httpx, "get", get)
    assert geocode_address("Karl-Liebknecht-Straße 1", near=(51.34, 12.37)) == (51.35, 12.38)
    assert seen["bounded"] == 1
    assert seen["viewbox"] == "12.0200,51.0900,12.7200,51.5900"


def test_geocode_no_result_returns_none(monkeypatch):
    get, _ = _fake_get([])
    monkeypatch.setattr(geocode.httpx, "get", get)
    assert geocode_address("Gibtsnicht 99", near=(53.5, 10.0)) is None


def test_geocode_transient_failure_returns_none(monkeypatch):
    def _get(url, params=None, headers=None, timeout=None):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(geocode.httpx, "get", _get)
    assert geocode_address("Neue Allee 7") is None


def test_geocode_returns_none_on_a_non_json_200(monkeypatch):
    def get(url, params=None, headers=None, timeout=None):
        return httpx.Response(200, text="<html>busy</html>", request=httpx.Request("GET", url))

    monkeypatch.setattr(geocode.httpx, "get", get)
    assert geocode_address("Neue Allee 7, Hamburg") is None


def test_lookup_address_formats_nominatim_details(monkeypatch):
    get, seen = _fake_get([{"address": {"road": "Am Schlossteich", "village": "Friedrichsruh",
                                        "city": "Aumühle", "postcode": "21521"}}])
    monkeypatch.setattr(geocode.httpx, "get", get)
    monkeypatch.setattr(geocode, "_last_lookup", 0.0)
    assert geocode.lookup_address("node/7924110466") == "Am Schlossteich, 21521 Aumühle"
    assert seen["osm_ids"] == "N7924110466"


def test_lookup_address_none_found_vs_failure(monkeypatch):
    monkeypatch.setattr(geocode, "_last_lookup", 0.0)
    monkeypatch.setattr(geocode.httpx, "get", _fake_get([])[0])
    assert geocode.lookup_address("way/5") == ""
    assert geocode.lookup_address("community/some-bar") == ""

    def boom(*a, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(geocode, "_last_lookup", 0.0)
    monkeypatch.setattr(geocode.httpx, "get", boom)
    assert geocode.lookup_address("node/1") is None


def test_lookup_address_never_waits_for_the_slot(monkeypatch):
    monkeypatch.setattr(geocode.httpx, "get", _fake_get([])[0])
    with geocode._lookup_lock:
        assert geocode.lookup_address("node/1") is None
