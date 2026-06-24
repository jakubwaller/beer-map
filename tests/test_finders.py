from pipeline.finders.base import BaseFinder
from pipeline.finders.ratsherrn import RatsherrnFinder
from pipeline.models import FinderEntry

FIXTURE = """
<html><body>
  <section><h2>Altes Mädchen</h2><p>Lagerstraße 28b, 20357 Hamburg</p></section>
  <section><h2>Ratsherrn Bar Schanze</h2><p>Lagerstraße 30a, 20357 Hamburg</p></section>
  <section><h2>Dolden Mädel Berlin</h2><p>Skalitzer Straße 25, 10999 Berlin</p></section>
</body></html>
"""


def test_basefinder_run_calls_fetch_then_parse():
    class Fake(BaseFinder):
        brand = "Test"
        def fetch(self):
            return "x"
        def parse(self, raw):
            return [FinderEntry(name="Foo", brand=self.brand)]
    assert Fake().run() == [FinderEntry(name="Foo", brand="Test")]


def test_ratsherrn_parses_hamburg_venues_as_fassbier():
    entries = RatsherrnFinder().parse(FIXTURE)
    assert {e.name for e in entries} == {"Altes Mädchen", "Ratsherrn Bar Schanze"}  # Berlin dropped
    for e in entries:
        assert e.brand == "Ratsherrn"
        assert e.serving == "fass"
        assert "Hamburg" in (e.address or "")


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
