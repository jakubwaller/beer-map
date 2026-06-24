from pipeline.matching import match_entry, match_entries
from pipeline.models import FinderEntry, Venue

VENUES = [
    Venue("node/10", "Altes Mädchen", 53.5635, 9.9650),
    Venue("node/11", "Irgendeine Kneipe", 53.6000, 10.0500),
]


def test_match_by_fuzzy_name():
    v = match_entry(FinderEntry(name="Altes Maedchen", brand="Ratsherrn"), VENUES)
    assert v is not None and v.osm_id == "node/10"


def test_no_match_returns_none():
    assert match_entry(FinderEntry(name="Völlig Anderer Laden", brand="X"), VENUES) is None


def test_match_entries_splits_matched_and_unmatched():
    entries = [FinderEntry(name="Altes Mädchen", brand="R"),
               FinderEntry(name="Unbekannt", brand="R")]
    matched, unmatched = match_entries(entries, VENUES)
    assert [v.osm_id for _, v in matched] == ["node/10"]
    assert [e.name for e in unmatched] == ["Unbekannt"]
