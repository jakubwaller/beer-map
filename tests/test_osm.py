from datetime import datetime, timedelta, timezone

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
                  "website": "https://quelle.example",
                  "opening_hours": "Mo-Fr 16:00-01:00; Sa,Su 14:00-02:00"}},
        {"type": "way", "id": 2, "center": {"lat": 53.56, "lon": 10.01},
         "tags": {"amenity": "bar", "name": "Eckkneipe", "brewery": "Astra;Holsten"}},
        {"type": "node", "id": 3, "lat": 53.5, "lon": 10.0, "tags": {"amenity": "cafe"}},
        # comma-separated list (seen in the wild) with a junk word mixed in
        {"type": "node", "id": 4, "lat": 53.57, "lon": 10.02,
         "tags": {"amenity": "pub", "name": "Kombi",
                  "brewery": "Dithmarscher,Holsten, crafted"}},
    ]
}


def test_parse_extracts_venues_and_brewery_edges():
    venues, edges = parse_overpass(SAMPLE)
    by_id = {v.osm_id: v for v in venues}
    assert set(by_id) == {"node/1", "way/2", "node/4"}  # nameless cafe dropped
    assert by_id["node/1"].address == "Lange Reihe 5, 20099 Hamburg"
    assert by_id["way/2"].lat == 53.56  # uses center for ways
    assert by_id["node/1"].opening_hours == "Mo-Fr 16:00-01:00; Sa,Su 14:00-02:00"
    assert by_id["way/2"].opening_hours is None  # untagged stays empty
    pairs = {(e.venue_osm_id, e.brand) for e in edges}
    assert pairs == {("node/1", "Ratsherrn"), ("way/2", "Astra"), ("way/2", "Holsten"),
                     ("node/4", "Dithmarscher"), ("node/4", "Holsten")}
    assert all(e.source == "osm" and e.serving == "unknown" for e in edges)


def _fake_get(sequence):
    """Return a stand-in for httpx.get that yields one entry of `sequence` per
    call (an int status code -> Response with an empty element list, a dict -> a
    200 carrying that body, an Exception -> raised) and records the URLs it was
    hit with."""
    seen = []

    def _get(url, params=None, headers=None, timeout=None):
        seen.append(url)
        item = sequence[len(seen) - 1]
        if isinstance(item, Exception):
            raise item
        status, body = (item, {"elements": []}) if isinstance(item, int) else (200, item)
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return _get, seen


def _aged(days: float, elements=()) -> dict:
    """An Overpass answer whose database is `days` old — the shape a mirror
    serving from a frozen import returns, HTTP 200 and all."""
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return {"osm3s": {"timestamp_osm_base": ts.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "elements": list(elements)}


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


def test_fetch_raises_when_all_mirrors_exhausted(monkeypatch, capsys):
    get, seen = _fake_get([504, 504, 504, 504])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=2, backoff=0)
    assert seen == ["http://m1", "http://m1", "http://m2", "http://m2"]
    # Both excuses are logged, not just the last mirror's: when the fallbacks
    # are broken too, why the *main* instance failed is the useful half.
    err = capsys.readouterr().err
    assert "http://m1: gave up after 2 attempts (HTTP 504)" in err
    assert "http://m2: gave up after 2 attempts (HTTP 504)" in err


def test_check_fresh_accepts_recent_and_missing_timestamp():
    osm.check_fresh(_aged(0.2), "http://m1")           # this morning's import
    osm.check_fresh({"elements": []}, "http://m1")     # frontend reports none


def test_check_fresh_rejects_frozen_database():
    with pytest.raises(osm.StaleMirror, match="77 days old"):
        osm.check_fresh(_aged(77), "http://m1")
    # the window is configurable, and a 30 h old database fails a 24 h one
    with pytest.raises(osm.StaleMirror):
        osm.check_fresh(_aged(1.25), "http://m1", max_age_h=24)
    osm.check_fresh(_aged(1.25), "http://m1", max_age_h=48)


def test_fetch_skips_stale_mirror_without_retrying(monkeypatch, capsys):
    get, seen = _fake_get([_aged(77), _aged(0.1, [{"type": "node", "id": 7}])])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    data = osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=3, backoff=0)
    assert data["elements"] == [{"type": "node", "id": 7}]
    assert seen == ["http://m1", "http://m2"]  # m1 tried once, not three times
    assert "database is 77 days old" in capsys.readouterr().err


def test_fetch_raises_stale_when_no_mirror_is_fresh(monkeypatch):
    get, seen = _fake_get([_aged(77), _aged(24)])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(osm.StaleMirror):
        osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=3, backoff=0)
    assert seen == ["http://m1", "http://m2"]


def test_fetch_hands_a_timeout_remark_to_the_next_mirror(monkeypatch):
    # HTTP 200 with a fragment of the answer and a remark admitting the timeout.
    get, seen = _fake_get([{"elements": [], "remark": 'runtime error: Query timed out in "query"'},
                           {"elements": [{"type": "node", "id": 7}]}])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    data = osm.fetch_overpass(urls=["http://m1", "http://m2"], retries=3, backoff=0)
    assert data["elements"] == [{"type": "node", "id": 7}]
    assert seen == ["http://m1", "http://m2"]  # no point asking m1 again


BUSY_STATUS = ("Connected as: 1234\nCurrent time: 2026-08-16T12:00:00Z\n"
               "Rate limit: 2\n"
               "Slot available after: 2026-08-16T12:00:37Z, in 37 seconds.\n"
               "Slot available after: 2026-08-16T12:01:02Z, in 62 seconds.\n")


MAIN = "https://overpass-api.de/api/interpreter"


def _status(text):
    def _get(*a, **k):
        return httpx.Response(200, text=text)
    return _get


def test_slot_wait_parses_the_main_instance_status_page():
    free = _status("Rate limit: 2\n2 slots available now.\n")
    assert osm.slot_wait_s(MAIN, get=free) == 0
    assert osm.slot_wait_s(MAIN, get=_status(BUSY_STATUS)) == 38  # earlier slot + 1 s


def test_slot_wait_is_capped_and_zero_for_mirrors_or_errors():
    far = _status("Slot available after: 2026-08-16T12:15:00Z, in 900 seconds.")
    assert osm.slot_wait_s(MAIN, get=far) == osm.OVERPASS_SLOT_WAIT_MAX_S

    def boom(*a, **k):
        raise httpx.ConnectError("status page down")

    assert osm.slot_wait_s(MAIN, get=boom) == 0  # unreadable: just try the query

    def never(*a, **k):
        raise AssertionError("mirrors have no /api/status — must not be asked")

    assert osm.slot_wait_s("https://overpass.kumi.systems/api/interpreter", get=never) == 0


def test_fetch_sleeps_the_reported_slot_wait_before_querying(monkeypatch):
    slept, asked = [], []

    def get(url, params=None, headers=None, timeout=None):
        asked.append(url)
        if url.endswith("/api/status"):
            return httpx.Response(200, text=BUSY_STATUS)
        return httpx.Response(200, json={"elements": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", slept.append)
    assert osm.fetch_overpass(urls=[MAIN], retries=1, backoff=0) == {"elements": []}
    assert slept == [38]  # waited the announced slot out instead of eating a 429
    assert asked == ["https://overpass-api.de/api/status", MAIN]


# --- circuit breaker ---------------------------------------------------------

def _refused(url="http://m1"):
    return httpx.ConnectError("[Errno 111] Connection refused",
                              request=httpx.Request("GET", url))


def test_refused_connection_rests_the_host_without_retrying(monkeypatch, capsys):
    # What a firewall ban looks like: the port never opens. Three more knocks
    # two seconds apart are pointless and exactly what a ban punishes.
    get, seen = _fake_get([_refused(), 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    assert osm.fetch_overpass("out;", urls=["http://m1", "http://m2"],
                              retries=3, backoff=0) == {"elements": []}
    assert seen == ["http://m1", "http://m2"]  # one knock on m1, then straight to m2
    assert osm.is_tripped("http://m1") and not osm.is_tripped("http://m2")
    assert "WARN http://m1: resting it for 15 min" in capsys.readouterr().err


def test_timeouts_and_resets_are_still_retried(monkeypatch):
    req = httpx.Request("GET", "http://m1")
    get, seen = _fake_get([httpx.ConnectTimeout("slow", request=req),
                           httpx.ReadError("reset", request=req), 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    assert osm.fetch_overpass("out;", urls=["http://m1"], retries=3, backoff=0) == {"elements": []}
    assert seen == ["http://m1"] * 3 and not osm.is_tripped("http://m1")


def test_host_rests_after_three_queries_in_a_row_exhaust_their_retries(monkeypatch):
    get, seen = _fake_get([504, 504, 200, 504, 504, 200, 504, 504, 200, 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    urls = ["http://m1", "http://m2"]
    for _ in range(3):
        osm.fetch_overpass("out;", urls=urls, retries=2, backoff=0)
    assert osm.is_tripped("http://m1") and not osm.is_tripped("http://m2")
    osm.fetch_overpass("out;", urls=urls, retries=2, backoff=0)
    assert seen[-1] == "http://m2" and seen.count("http://m1") == 6  # m1 not contacted again


def test_a_successful_answer_clears_the_strikes(monkeypatch):
    get, seen = _fake_get([504, 504, 200, 504, 504, 200, 200, 504, 504, 200])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    urls = ["http://m1", "http://m2"]
    for _ in range(4):
        osm.fetch_overpass("out;", urls=urls, retries=2, backoff=0)
    assert not osm.is_tripped("http://m1")  # two strikes, a success, one strike


def test_every_host_resting_raises_at_once_without_contact(monkeypatch):
    get, seen = _fake_get([_refused("http://m1"), _refused("http://m2")])
    monkeypatch.setattr(osm.httpx, "get", get)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    urls = ["http://m1", "http://m2"]
    with pytest.raises(httpx.ConnectError):
        osm.fetch_overpass("out;", urls=urls, retries=3, backoff=0)
    assert seen == ["http://m1", "http://m2"]
    with pytest.raises(osm.OverpassUnavailable, match="next one back in 15 min"):
        osm.fetch_overpass("out;", urls=urls, retries=3, backoff=0)
    assert seen == ["http://m1", "http://m2"]  # not a single further request
    assert 0 < osm.breaker_wait_s(urls) <= osm.OVERPASS_TRIP_COOLDOWN_S


def test_rest_doubles_with_each_consecutive_trip_and_caps(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(osm.time, "monotonic", lambda: clock[0])
    rests = []
    for _ in range(5):
        osm._trip("http://m1", "refused")
        rests.append(osm._state("http://m1")["until"] - clock[0])
        clock[0] = osm._state("http://m1")["until"] + 1  # let it expire
    assert rests == [900, 1800, 3600, 7200, 7200]
    assert not osm.is_tripped("http://m1")
    osm._recover("http://m1")
    osm._trip("http://m1", "refused")
    assert osm._state("http://m1")["until"] - clock[0] == 900  # a success resets the ladder
