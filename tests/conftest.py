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
