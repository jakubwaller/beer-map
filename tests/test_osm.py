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
