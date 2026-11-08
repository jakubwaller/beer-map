import pytest


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """osm.py's circuit breaker is module state; one test's failures must
    not rest a host for the next."""
    from pipeline import osm
    osm.reset_breaker()
    yield
    osm.reset_breaker()


@pytest.fixture(autouse=True)
def _offline_dedupe(monkeypatch):
    """/admin's duplicate check would geocode and ask the OSM API; tests stay
    offline (OSM "down", nothing geocodes) unless a test patches these."""
    import httpx
    from pipeline import dedupe

    def down(*a, **k):
        raise httpx.ConnectError("offline in tests")
    monkeypatch.setattr(dedupe, "_get_map", down)
    monkeypatch.setattr(dedupe, "geocode_address", lambda *a, **k: None)
    monkeypatch.setattr(dedupe, "GEOCODE_INTERVAL_S", 0.0)


@pytest.fixture(autouse=True)
def _offline_rekey(monkeypatch):
    """The re-key asks the OSM API before it merges; in tests OSM is down
    unless a test passes its own api (tests/test_rekey.py's `_osm`)."""
    import httpx
    from pipeline import osm_push, rekey

    def down(request):
        raise httpx.ConnectError("offline in tests")
    monkeypatch.setattr(rekey, "OsmApi", lambda: osm_push.OsmApi(
        "https://osm.test", transport=httpx.MockTransport(down)))
    monkeypatch.setattr(rekey, "LOOKUP_DELAY_S", 0.0)
