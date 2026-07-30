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


def test_name_only_tie_across_cities_is_dropped_as_ambiguous():
    # Same name in Hamburg and Leipzig: without coordinates there is no way to
    # tell which one the finder meant, so the entry must stay unmatched.
    venues = [Venue("node/1", "Gloria", 53.56, 9.96),
              Venue("node/2", "Gloria", 51.34, 12.37)]
    assert match_entry(FinderEntry(name="Gloria", brand="X"), venues) is None


def test_coordinates_disambiguate_same_named_venues():
    venues = [Venue("node/1", "Gloria", 53.56, 9.96),
              Venue("node/2", "Gloria", 51.34, 12.37)]
    v = match_entry(FinderEntry(name="Gloria", brand="X", lat=51.3401, lon=12.3702), venues)
    assert v is not None and v.osm_id == "node/2"


def test_match_entries_splits_matched_and_unmatched():
    entries = [FinderEntry(name="Altes Mädchen", brand="R"),
               FinderEntry(name="Unbekannt", brand="R")]
    matched, unmatched = match_entries(entries, VENUES)
    assert [v.osm_id for _, v in matched] == ["node/10"]
    assert [e.name for e in unmatched] == ["Unbekannt"]
