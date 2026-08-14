from pipeline.country import build_tile_ql, grid, sweep_country
from pipeline.db import get_connection


def test_grid_covers_bbox_and_clamps_edge_tiles():
    tiles = grid((47.2, 5.8, 55.1, 19.0), 1.0)
    assert len(tiles) == 8 * 14
    assert tiles[0] == (47.2, 5.8, 48.2, 6.8)
    assert tiles[-1] == (54.2, 18.8, 55.1, 19.0)  # clamped to the bbox edge
    assert all(t[2] <= 55.1 and t[3] <= 19.0 for t in tiles)


def test_tile_ql_clips_to_countries():
    ql = build_tile_ql(50.0, 14.0, 51.0, 15.0)
    assert "(50.0,14.0,51.0,15.0)" in ql
    assert 'ISO3166-1' in ql and "(DE|CZ|AT)" in ql
    assert "amenity" in ql and "out center tags" in ql


def _one_tile_bbox():
    return (50.0, 14.0, 50.5, 14.5)  # single grid tile at tile_deg=1.0


def test_sweep_upserts_venues_and_brewery_edges(tmp_path):
    db = str(tmp_path / "t.sqlite")

    def fetch(ql):
        return {"elements": [
            {"type": "node", "id": 1, "lat": 50.1, "lon": 14.4,
             "tags": {"name": "U Fleků", "amenity": "pub", "brewery": "U Fleků"}},
            {"type": "way", "id": 2, "center": {"lat": 50.2, "lon": 14.45},
             "tags": {"name": "Dorfkrug", "amenity": "restaurant"}},
        ]}

    stats = sweep_country(db_path=db, bbox=_one_tile_bbox(), fetch=fetch,
                          sleep_s=0, today="2026-08-11")
    assert stats["tiles"] == 1 and stats["venues"] == 2 and stats["edges"] == 1
    conn = get_connection(db)
    assert {r["name"] for r in conn.execute("SELECT name FROM venues")} == \
        {"U Fleků", "Dorfkrug"}
    edge = conn.execute(
        "SELECT b.name brand, vb.source FROM venue_brand vb "
        "JOIN brands b ON b.id=vb.brand_id").fetchone()
    assert edge["brand"] == "U Fleků" and edge["source"] == "osm"
    assert conn.execute("SELECT COUNT(*) c FROM country_tiles").fetchone()["c"] == 1


def test_resume_skips_recently_fetched_tiles(tmp_path):
    db = str(tmp_path / "t.sqlite")
    calls = []

    def fetch(ql):
        calls.append(ql)
        return {"elements": []}

    assert sweep_country(db_path=db, bbox=_one_tile_bbox(), fetch=fetch,
                         sleep_s=0)["tiles"] == 1
    assert len(calls) == 1
    # --resume within the window: nothing refetched
    again = sweep_country(db_path=db, bbox=_one_tile_bbox(), fetch=fetch,
                          sleep_s=0, resume=True)
    assert again["skipped"] == 1 and again["tiles"] == 0 and len(calls) == 1
    # a plain (weekly-cron) run refreshes everything
    assert sweep_country(db_path=db, bbox=_one_tile_bbox(), fetch=fetch,
                         sleep_s=0)["tiles"] == 1
    assert len(calls) == 2


def test_failing_tile_is_quartered_and_quarters_succeed(tmp_path):
    def fetch(ql):
        if "(50.0,14.0,51.0,15.0)" in ql:  # the full 1°x1° tile times out
            raise RuntimeError("gateway timeout")
        return {"elements": []}

    stats = sweep_country(db_path=str(tmp_path / "t.sqlite"),
                          bbox=(50.0, 14.0, 51.0, 15.0), fetch=fetch, sleep_s=0)
    assert stats["split"] == 1 and stats["tiles"] == 4 and stats["failed"] == 0


def test_timeout_remark_is_a_failure_not_partial_data(tmp_path):
    # Overpass can answer 200 with a partial element list and only a "remark"
    # admitting the timeout — that must never count as a fetched tile.
    def fetch(ql):
        return {"elements": [], "remark": 'runtime error: Query timed out in "query"'}

    stats = sweep_country(db_path=str(tmp_path / "t.sqlite"),
                          bbox=(50.0, 14.0, 50.2, 14.2), fetch=fetch, sleep_s=0)
    assert stats["failed"] == 1 and stats["tiles"] == 0
    assert stats["failures"] == ["50,14,50.2,14.2"]
