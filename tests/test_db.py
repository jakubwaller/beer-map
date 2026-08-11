from pipeline.config import SKIP_BRANDS, normalize_brand
from pipeline.db import (
    get_connection, init_db, upsert_venue, upsert_brand,
    upsert_edge, delete_edges, fetch_venues_with_brands,
    fetch_gray_in_bbox, fold, search_venues_db,
    insert_submission, list_submissions, get_submission,
    set_submission_status, count_submissions_since,
    update_venue_address, set_venue_hidden, renormalize_brands,
    scrub_plaintext_ips,
)
from pipeline.models import Venue


def _sub(**kw):
    base = dict(kind="add", venue_osm_id="node/1", venue_name="Bar X", lat=None, lon=None,
                brand="Astra", serving="fass", note=None, submitter_ip="1.2.3.4")
    base.update(kw)
    return base


def test_submission_crud_and_status():
    conn = _conn()
    sid = insert_submission(conn, _sub(), "2026-06-24T10:00:00")
    pending = list_submissions(conn, "pending")
    assert len(pending) == 1 and pending[0]["brand"] == "Astra" and pending[0]["status"] == "pending"
    set_submission_status(conn, sid, "approved", "2026-06-24T11:00:00")
    assert get_submission(conn, sid)["status"] == "approved"
    assert list_submissions(conn, "pending") == []


def test_count_submissions_since():
    conn = _conn()
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:00:00")
    insert_submission(conn, _sub(submitter_ip="9.9.9.9"), "2026-06-24T10:30:00")
    insert_submission(conn, _sub(submitter_ip="8.8.8.8"), "2026-06-24T10:30:00")
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T10:15:00") == 1
    assert count_submissions_since(conn, "9.9.9.9", "2026-06-24T09:00:00") == 2


def test_community_ranks_between_manual_and_finder():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    for src in ("osm", "finder:Ratsherrn", "community", "manual"):
        bid = upsert_brand(conn, f"B-{src}")
        upsert_edge(conn, vid, bid, src, "2026-06-24")
    order = [b["source"] for b in fetch_venues_with_brands(conn)[0]["brands"]]
    assert order == ["manual", "community", "finder:Ratsherrn", "osm"]


def _conn():
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def test_upsert_venue_is_idempotent_by_osm_id():
    conn = _conn()
    id1 = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    id2 = upsert_venue(conn, Venue("node/1", "Bar X (renamed)", 53.5, 10.0), "2026-06-25")
    assert id1 == id2
    rows = conn.execute("select name from venues").fetchall()
    assert len(rows) == 1 and rows[0]["name"] == "Bar X (renamed)"


def test_edge_with_serving_and_provenance_roundtrips():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "manual", "2026-06-24", serving="tank")
    out = fetch_venues_with_brands(conn)
    assert out[0]["brands"] == [
        {"brand": "Pilsner Urquell", "source": "manual",
         "serving": "tank", "beer": None, "last_seen": "2026-06-24"}
    ]


def test_delete_edges_removes_stale_links():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "WALD", 53.5, 10.0), "2026-06-24")
    bid = upsert_brand(conn, "Pilsner Urquell")
    upsert_edge(conn, vid, bid, "finder:Pilsner Urquell", "2026-06-24", serving="tank")
    assert delete_edges(conn, vid, bid) == 1
    assert fetch_venues_with_brands(conn)[0]["brands"] == []


def test_update_venue_address_overrides_osm_value():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="Old St 1"), "2026-06-24")
    assert update_venue_address(conn, "node/1", "New Allee 2") == 1
    assert fetch_venues_with_brands(conn)[0]["address"] == "New Allee 2"
    # A re-import from OSM overwrites the address, but it can be re-applied.
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="Old St 1"), "2026-06-25")
    assert fetch_venues_with_brands(conn)[0]["address"] == "Old St 1"
    update_venue_address(conn, "node/1", "New Allee 2")
    assert fetch_venues_with_brands(conn)[0]["address"] == "New Allee 2"


def test_hidden_venue_excluded_from_export_but_survives_reimport():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Closed Bar", 53.5, 10.0), "2026-06-24")
    upsert_venue(conn, Venue("node/2", "Open Bar", 53.6, 10.1), "2026-06-24")
    assert set_venue_hidden(conn, "node/1", True) == 1
    names = [v["name"] for v in fetch_venues_with_brands(conn)]
    assert names == ["Open Bar"]
    # OSM re-import must not resurrect a hidden venue (upsert leaves hidden alone).
    upsert_venue(conn, Venue("node/1", "Closed Bar", 53.5, 10.0), "2026-06-25")
    assert [v["name"] for v in fetch_venues_with_brands(conn)] == ["Open Bar"]
    assert set_venue_hidden(conn, "node/1", False) == 1
    assert len(fetch_venues_with_brands(conn)) == 2


def test_edge_beer_roundtrips():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-27")
    bid = upsert_brand(conn, "Ratsherrn")
    upsert_edge(conn, vid, bid, "manual", "2026-06-27", serving="fass", beer="Matrosenschluck")
    out = fetch_venues_with_brands(conn)[0]["brands"][0]
    assert out["beer"] == "Matrosenschluck" and out["serving"] == "fass"
    # default is NULL when no specific beer is given
    bid2 = upsert_brand(conn, "Astra")
    upsert_edge(conn, vid, bid2, "osm", "2026-06-27", serving="unknown")
    astra = [b for b in fetch_venues_with_brands(conn)[0]["brands"] if b["brand"] == "Astra"][0]
    assert astra["beer"] is None


def test_multiple_beers_same_brand_per_venue():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-06-27")
    bid = upsert_brand(conn, "Augustiner")
    upsert_edge(conn, vid, bid, "manual", "2026-06-27", serving="fass", beer="Edelstoff")
    upsert_edge(conn, vid, bid, "manual", "2026-06-27", serving="fass", beer="Hell")
    beers = {b["beer"] for b in fetch_venues_with_brands(conn)[0]["brands"]}
    assert beers == {"Edelstoff", "Hell"}
    # re-upserting the same (brand, beer) updates in place, no duplicate
    upsert_edge(conn, vid, bid, "manual", "2026-06-28", serving="tank", beer="Edelstoff")
    edel = [b for b in fetch_venues_with_brands(conn)[0]["brands"] if b["beer"] == "Edelstoff"]
    assert len(edel) == 1 and edel[0]["serving"] == "tank"


def _brand_names(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM brands")}


def test_renormalize_merges_spelling_variants():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-07-19")
    vid2 = upsert_venue(conn, Venue("node/2", "Bar Y", 53.6, 10.1), "2026-07-19")
    # rows written before the aliases existed
    upsert_edge(conn, vid, upsert_brand(conn, "jever"), "osm", "2026-07-19")
    upsert_edge(conn, vid2, upsert_brand(conn, "Jever"), "manual", "2026-07-19", serving="fass")
    upsert_edge(conn, vid, upsert_brand(conn, "Budweiser"), "osm", "2026-07-19")

    assert renormalize_brands(conn, normalize_brand, SKIP_BRANDS) == 2
    assert _brand_names(conn) == {"Jever", "Budweiser Budvar"}
    by_venue = {v["osm_id"]: v["brands"] for v in fetch_venues_with_brands(conn)}
    assert {b["brand"] for b in by_venue["node/1"]} == {"Jever", "Budweiser Budvar"}
    assert by_venue["node/2"][0]["serving"] == "fass"  # edge survived the remap


def test_renormalize_merge_keeps_known_serving_on_conflict():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-07-19")
    # canonical edge knows the serving, variant doesn't — and vice versa
    upsert_edge(conn, vid, upsert_brand(conn, "Guinness"), "osm", "2026-07-19", serving="fass")
    upsert_edge(conn, vid, upsert_brand(conn, "guinness"), "osm", "2026-07-19")
    upsert_edge(conn, vid, upsert_brand(conn, "Jever"), "manual", "2026-07-19")
    upsert_edge(conn, vid, upsert_brand(conn, "jever"), "manual", "2026-07-19", serving="tank")
    renormalize_brands(conn, normalize_brand, SKIP_BRANDS)
    by_brand = {b["brand"]: b for b in fetch_venues_with_brands(conn)[0]["brands"]}
    assert set(by_brand) == {"Guinness", "Jever"}
    assert by_brand["Guinness"]["serving"] == "fass"
    assert by_brand["Jever"]["serving"] == "tank"


def test_renormalize_splits_multi_brand_rows_and_drops_junk():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-07-19")
    upsert_edge(conn, vid, upsert_brand(conn, "Dithmarscher,Holsten, Flensburger"),
                "osm", "2026-07-19")
    upsert_edge(conn, vid, upsert_brand(conn, "crafted"), "osm", "2026-07-19")
    upsert_brand(conn, "Orphan Bräu")  # no edges at all
    renormalize_brands(conn, normalize_brand, SKIP_BRANDS)
    assert _brand_names(conn) == {"Dithmarscher", "Holsten", "Flensburger"}
    brands = {b["brand"] for b in fetch_venues_with_brands(conn)[0]["brands"]}
    assert brands == {"Dithmarscher", "Holsten", "Flensburger"}


def test_renormalize_is_idempotent():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0), "2026-07-19")
    upsert_edge(conn, vid, upsert_brand(conn, "einbecker"), "osm", "2026-07-19")
    assert renormalize_brands(conn, normalize_brand, SKIP_BRANDS) == 1
    assert renormalize_brands(conn, normalize_brand, SKIP_BRANDS) == 0
    assert _brand_names(conn) == {"Einbecker"}


def test_migration_moves_beer_into_pk_preserving_rows():
    conn = get_connection(":memory:")
    conn.executescript(
        "CREATE TABLE venues (id INTEGER PRIMARY KEY, osm_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,"
        " lat REAL NOT NULL, lon REAL NOT NULL, address TEXT, website TEXT,"
        " hidden INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);"
        "CREATE TABLE brands (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);"
        "CREATE TABLE venue_brand (venue_id INTEGER, brand_id INTEGER, source TEXT NOT NULL,"
        " serving TEXT NOT NULL DEFAULT 'unknown', beer TEXT, confidence REAL NOT NULL DEFAULT 1.0,"
        " first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,"
        " PRIMARY KEY (venue_id, brand_id, source));"
    )
    conn.execute("INSERT INTO venues VALUES (1,'node/1','Bar',53.5,10.0,NULL,NULL,0,'d')")
    conn.execute("INSERT INTO brands VALUES (1,'Augustiner')")
    conn.execute("INSERT INTO venue_brand (venue_id,brand_id,source,serving,beer,confidence,"
                 "first_seen,last_seen) VALUES (1,1,'manual','fass','Edelstoff',1.0,'d','d')")
    conn.commit()

    init_db(conn)  # should rebuild venue_brand with beer in the PK

    assert fetch_venues_with_brands(conn)[0]["brands"][0]["beer"] == "Edelstoff"  # preserved
    pk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(venue_brand)") if r["pk"]]
    assert "beer" in pk_cols
    upsert_edge(conn, 1, 1, "manual", "2026-06-27", serving="fass", beer="Hell")  # now allowed
    assert {b["beer"] for b in fetch_venues_with_brands(conn)[0]["brands"]} == {"Edelstoff", "Hell"}


def test_fold_matches_the_frontend_folding():
    # MUST agree with fold() in web/datasource.js — server and client search
    # the same folded strings.
    assert fold("Küche & Café „St.Pauli“") == "kuche cafe st pauli"
    assert fold("Straßenbräu") == "strassenbrau"
    assert fold(None) == ""


def test_search_venues_db_folds_ranks_and_spans_fields():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Zum Goldenen Handwerk", 51.0, 13.7,
                             address="Dresden"), "2026-08-11")
    upsert_venue(conn, Venue("node/2", "Handwerkerhof", 49.4, 11.0,
                             address="Nürnberg"), "2026-08-11")
    upsert_venue(conn, Venue("node/3", "Café Küche", 53.5, 10.0,
                             address="Hamburg"), "2026-08-11")
    names = [v["name"] for v in search_venues_db(conn, "handwerk")]
    assert set(names) == {"Zum Goldenen Handwerk", "Handwerkerhof"}
    assert names[0] == "Handwerkerhof"          # name-prefix hit ranks first
    assert [v["name"] for v in search_venues_db(conn, "kuche")] == ["Café Küche"]
    # tokens may land in different fields (name + address)
    assert [v["name"] for v in search_venues_db(conn, "goldenen dresden")] == \
        ["Zum Goldenen Handwerk"]
    assert search_venues_db(conn, "") == []
    assert search_venues_db(conn, "…") == []    # folds to nothing


def test_search_key_backfilled_for_legacy_rows():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Bar X", 53.5, 10.0, address="Beim Grünen Jäger 1"),
                 "2026-08-11")
    conn.execute("UPDATE venues SET search_key=NULL")  # row from before the column
    init_db(conn)
    assert conn.execute("SELECT search_key FROM venues").fetchone()["search_key"] == \
        "bar x beim grunen jager 1"


def test_fetch_gray_in_bbox_excludes_branded_hidden_and_outside():
    conn = _conn()
    upsert_venue(conn, Venue("node/1", "Gray In", 50.1, 14.1), "2026-08-11")
    upsert_venue(conn, Venue("node/2", "Gray Outside", 52.0, 13.0), "2026-08-11")
    vid = upsert_venue(conn, Venue("node/3", "Branded In", 50.15, 14.15), "2026-08-11")
    upsert_edge(conn, vid, upsert_brand(conn, "Astra"), "osm", "2026-08-11")
    upsert_venue(conn, Venue("node/4", "Hidden In", 50.12, 14.12), "2026-08-11")
    set_venue_hidden(conn, "node/4", True)
    rows = fetch_gray_in_bbox(conn, 50.0, 14.0, 50.5, 14.5)
    assert [r["name"] for r in rows] == ["Gray In"]
    assert rows[0]["brands"] == []


def test_fetch_venues_with_brands_branded_only():
    conn = _conn()
    vid = upsert_venue(conn, Venue("node/1", "Branded", 50.0, 14.0), "2026-08-11")
    upsert_edge(conn, vid, upsert_brand(conn, "Astra"), "osm", "2026-08-11")
    upsert_venue(conn, Venue("node/2", "Gray", 50.1, 14.1), "2026-08-11")
    assert len(fetch_venues_with_brands(conn)) == 2
    assert [v["name"] for v in fetch_venues_with_brands(conn, branded_only=True)] == \
        ["Branded"]


def test_scrub_plaintext_ips_clears_legacy_rows():
    """Rows written before hashing existed must not keep a raw address on disk."""
    conn = _conn()
    insert_submission(conn, _sub(submitter_ip="1.2.3.4"), "2026-06-24T10:00:00")
    insert_submission(conn, _sub(submitter_ip="h:deadbeef"), "2026-06-24T10:00:00")
    assert scrub_plaintext_ips(conn) == 1
    stored = {r["submitter_ip"] for r in list_submissions(conn, "pending")}
    assert stored == {"", "h:deadbeef"}   # hashed row untouched, raw one blanked
    assert scrub_plaintext_ips(conn) == 0  # idempotent
