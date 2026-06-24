from __future__ import annotations

from datetime import date

from . import curation, osm
from .config import CURATION_PATH, DB_PATH, OUT_PATH, normalize_brand
from .db import get_connection, init_db, upsert_brand, upsert_edge, upsert_venue
from .export import export_geojson
from .finders import FINDERS
from .matching import match_entries


def _store_edge(conn, venue_id, brand, source, seen, serving="unknown"):
    bid = upsert_brand(conn, normalize_brand(brand))
    upsert_edge(conn, venue_id, bid, source, seen, serving=serving)


def run_pipeline(db_path=DB_PATH, out_path=OUT_PATH, curation_path=CURATION_PATH,
                 overpass_fetch=osm.fetch_overpass, finders=FINDERS, today=None):
    today = today or date.today().isoformat()
    conn = get_connection(db_path)
    init_db(conn)

    venues, osm_edges = osm.parse_overpass(overpass_fetch())
    id_by_osm = {v.osm_id: upsert_venue(conn, v, today) for v in venues}
    for e in osm_edges:
        _store_edge(conn, id_by_osm[e.venue_osm_id], e.brand, e.source, today, serving=e.serving)

    finder_edges = unmatched_total = 0
    for f in finders:
        matched, unmatched = match_entries(f.run(), venues)
        unmatched_total += len(unmatched)
        for entry, venue in matched:
            _store_edge(conn, id_by_osm[venue.osm_id], entry.brand,
                        f"finder:{f.brand}", today, serving=entry.serving)
            finder_edges += 1

    cur = curation.apply_curation(conn, curation.load_curation(curation_path), venues, today)
    conn.commit()
    exported = export_geojson(conn, out_path)
    return {
        "venues": len(venues), "osm_edges": len(osm_edges), "finder_edges": finder_edges,
        "unmatched": unmatched_total, "manual_added": cur["added"],
        "manual_removed": cur["removed"], "exported": exported,
    }


if __name__ == "__main__":
    print(run_pipeline())
