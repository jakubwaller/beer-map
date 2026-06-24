from pipeline.finders.base import BaseFinder
from pipeline.finders.ratsherrn import RatsherrnFinder
from pipeline.models import FinderEntry

FIXTURE = """
<div><h2>UNSERE LOCATIONS</h2></div>
<div class="card"><h3>Braugasthaus Altes Mädchen</h3></div>
<div class="card"><h3>RATSHERRN BAR MÜHLENKAMP</h3></div>
<div class="card"><h3>Dolden Mädel Berlin</h3></div>
"""


def test_basefinder_run_calls_fetch_then_parse():
    class Fake(BaseFinder):
        brand = "Test"
        def fetch(self):
            return "x"
        def parse(self, raw):
            return [FinderEntry(name="Foo", brand=self.brand)]
    assert Fake().run() == [FinderEntry(name="Foo", brand="Test")]


def test_ratsherrn_extracts_venue_headings_as_fassbier():
    entries = RatsherrnFinder().parse(FIXTURE)
    names = {e.name for e in entries}
    assert "Braugasthaus Altes Mädchen" in names
    assert "RATSHERRN BAR MÜHLENKAMP" in names
    assert "UNSERE LOCATIONS" not in names  # generic section title skipped
    for e in entries:
        assert e.brand == "Ratsherrn"
        assert e.serving == "fass"


PU_FIXTURE = """
<ul class="pubs">
  <li class="pub"><a href="/pubs/wald/"><h3>WALD</h3></a><span>Hamburg</span></li>
  <li class="pub"><a href="/pubs/gloria/"><h3>Gloria</h3></a><span>Hamburg</span></li>
  <li class="pub"><a href="/pubs/james-june/"><h3>James June</h3></a><span>Berlin</span></li>
</ul>
"""


def test_pilsner_urquell_parses_hamburg_tank_venues():
    from pipeline.finders.pilsner_urquell import PilsnerUrquellFinder
    entries = PilsnerUrquellFinder().parse(PU_FIXTURE)
    assert {e.name for e in entries} == {"WALD", "Gloria"}  # Berlin dropped
    for e in entries:
        assert e.brand == "Pilsner Urquell"
        assert e.serving == "tank"
