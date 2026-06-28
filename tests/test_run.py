import json

from pipeline.finders.base import BaseFinder
from pipeline.models import FinderEntry
from pipeline.run import run_pipeline

FAKE_OVERPASS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 53.5635, "lon": 9.9650,
         "tags": {"amenity": "pub", "name": "Altes Mädchen", "brewery": "Holsten"}},
        {"type": "node", "id": 2, "lat": 53.55, "lon": 9.97,
         "tags": {"amenity": "bar", "name": "WALD"}},
    ]
}


class FakeFinder(BaseFinder):
    brand = "Pilsner Urquell"
    serving = "tank"
    def run(self):
        return [FinderEntry(name="WALD", brand="Pilsner Urquell", serving="tank")]


def test_run_pipeline_applies_curation_over_finders(tmp_path):
    out = tmp_path / "venues.json"
    cur = tmp_path / "curation.yaml"
    cur.write_text(
        "- venue: WALD\n  brand: Pilsner Urquell\n  action: remove\n  verified: 2026-06-24\n"
        "- venue: WALD\n  brand: budvar\n  serving: tank\n  action: add\n  verified: 2026-06-24\n",
        encoding="utf-8",
    )
    summary = run_pipeline(
        db_path=":memory:", out_path=str(out), curation_path=str(cur),
        overpass_fetch=lambda: FAKE_OVERPASS, finders=[FakeFinder()], today="2026-06-24",
    )
    assert summary == {
        "venues": 2, "osm_edges": 1, "finder_edges": 1,
        "unmatched": 0, "manual_added": 1, "manual_removed": 1,
        "community": 0, "exported": 2,
    }
    fc = json.loads(out.read_text(encoding="utf-8"))
    by_name = {f["properties"]["name"]: f["properties"]["brands"] for f in fc["features"]}
    # Finder added PU@WALD, curation removed it and added Budvar -> only Budvar remains.
    assert by_name["WALD"] == [{"brand": "Budweiser Budvar", "source": "manual",
                                "serving": "tank", "beer": None, "last_seen": "2026-06-24"}]
    assert {b["brand"] for b in by_name["Altes Mädchen"]} == {"Holsten"}
