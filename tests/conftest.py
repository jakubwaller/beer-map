import pytest


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """osm.py's circuit breaker is module state; one test's failures must
    not rest a host for the next."""
    from pipeline import osm
    osm.reset_breaker()
    yield
    osm.reset_breaker()
