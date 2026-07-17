import httpx
import pytest

from pipeline import osm
from pipeline.osm import parse_overpass

SAMPLE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 53.55, "lon": 9.99,
         "tags": {"amenity": "pub", "name": "Zur Quelle", "brewery": "Ratsherrn",
                  "addr:street": "Lange Reihe", "addr:housenumber": "5",
                  "addr:postcode": "20099", "addr:city": "Hamburg",
                  "website": "https://quelle.example"}},
        {"type": "way", "id": 2, "center": {"lat": 53.56, "lon": 10.01},
         "tags": {"amenity": "bar", "name": "Eckkneipe", "brewery": "Astra;Holsten"}},
        {"type": "node", "id": 3, "lat": 53.5, "lon": 10.0, "tags": {"amenity": "cafe"}},
    ]
}


def test_parse_extracts_venues_and_brewery_edges():
    venues, edges = parse_overpass(SAMPLE)
    by_id = {v.osm_id: v for v in venues}
    assert set(by_id) == {"node/1", "way/2"}  # nameless cafe dropped
    assert by_id["node/1"].address == "Lange Reihe 5, 20099 Hamburg"
    assert by_id["way/2"].lat == 53.56  # uses center for ways
    pairs = {(e.venue_osm_id, e.brand) for e in edges}
    assert pairs == {("node/1", "Ratsherrn"), ("way/2", "Astra"), ("way/2", "Holsten")}
    assert all(e.source == "osm" and e.serving == "unknown" for e in edges)


def _fake_get(sequence):
    """Return a stand-in for httpx.get that yields one entry of `sequence` per
    call (an int status code -> Response, an Exception -> raised) and records the
    URLs it was hit with."""
    seen = []

    def _get(url, params=None, headers=None, timeout=None):
        seen.append(url)
        item = sequence[len(seen) - 1]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={"elements": []}, request=httpx.Request("GET", url))

    return _get, seen


def test_fetch_retries_transient_then_succeeds(monkeypatch):
    get, seen = _fake_get([503, 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    assert osm.fetch_overpass(urls=["http://m1"], retries=3, backoff=0) == {"elements": []}
    assert seen == ["http://m1", "http://m1"]  # retried the same mirror


def test_fetch_falls_over_to_next_mirror(monkeypatch):
    get, seen = _fake_get([406, 406, 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    assert osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=2, backoff=0) == {"elements": []}
    assert seen == ["http://m1", "http://m1", "http://m2"]  # m1 exhausted, then m2


def test_fetch_retries_transport_error(monkeypatch):
    get, seen = _fake_get([httpx.ReadTimeout("slow"), 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    assert osm.fetch_overpass(urls=["http://m1"], retries=2, backoff=0) == {"elements": []}
    assert seen == ["http://m1", "http://m1"]


def test_fetch_raises_on_non_transient_status(monkeypatch):
    get, seen = _fake_get([400])  # bad query — no mirror will differ
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=3, backoff=0)
    assert seen == ["http://m1"]  # aborted before retrying or falling over


def test_fetch_raises_when_all_mirrors_exhausted(monkeypatch):
    get, seen = _fake_get([504, 504, 504, 504])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=2, backoff=0)
    assert seen == ["http://m1", "http://m1", "http://m2", "http://m2"]
