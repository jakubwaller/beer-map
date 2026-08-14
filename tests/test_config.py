from pipeline.config import build_overpass_ql, normalize_brand


def test_overpass_ql_sweeps_cities_and_national_brewery_layer():
    ql = build_overpass_ql()
    assert '["name"="Hamburg"]["admin_level"="4"]' in ql
    assert '["name"="Leipzig"]["admin_level"="6"]' in ql
    assert '["name"="Berlin"]["admin_level"="4"]' in ql
    assert '["name"="München"]["admin_level"="6"]' in ql
    # Hannover's city boundary is admin_level 8 (level 6 is Region Hannover).
    assert '["name"="Hannover"]["admin_level"="8"]' in ql
    # Czech sweep cities: Praha doubles as its own kraj (level 4); the other
    # statutory cities sit at level 8.
    assert '["name"="Praha"]["admin_level"="4"]' in ql
    assert '["name"="Brno"]["admin_level"="8"]' in ql
    assert '["name"="Plzeň"]["admin_level"="8"]' in ql
    assert '["name"="Mladá Boleslav"]["admin_level"="8"]' in ql
    # Austrian sweep cities: Wien is its own Bundesland (level 4); the
    # Statutarstädte double as their Bezirk (level 6) — and the level filter is
    # what separates the city of Salzburg from the Land Salzburg (level 4).
    assert '["name"="Wien"]["admin_level"="4"]' in ql
    assert '["name"="Graz"]["admin_level"="6"]' in ql
    assert '["name"="Salzburg"]["admin_level"="6"]' in ql
    assert '["name"="Klagenfurt am Wörthersee"]["admin_level"="6"]' in ql
    # Nationwide only brewery-tagged venues — a full-country amenity sweep
    # would be ~250k elements and times out on Overpass.
    assert '"brewery"](area.countries)' in ql
    assert ql.count('nwr["amenity"') == 2
    # City names are not globally unique, so the sweep must stay inside the
    # covered countries (DE + CZ + AT).
    assert '(area.cities)(area.countries)' in ql
    assert '"ISO3166-1"~"^(DE|CZ|AT)$"' in ql


def test_overpass_ql_takes_custom_sweep_areas():
    ql = build_overpass_ql(sweep_areas=('["name"="Dresden"]["admin_level"="6"]',))
    assert '"Dresden"' in ql and '"Hamburg"' not in ql


def test_alias_lookup_is_case_insensitive():
    assert normalize_brand("Budweiser") == "Budweiser Budvar"
    assert normalize_brand("budweiser budvar") == "Budweiser Budvar"
    assert normalize_brand("Budějovický Budvar") == "Budweiser Budvar"
    assert normalize_brand("Velkopopovický Kozel") == "Kozel"
    assert normalize_brand("kozel") == "Kozel"
    assert normalize_brand("Plzeňský Prazdroj") == "Pilsner Urquell"
    assert normalize_brand("König Pilsener") == "König Pilsner"
    assert normalize_brand("königpilsener") == "König Pilsner"
    assert normalize_brand("Ratsherren") == "Ratsherrn"
    assert normalize_brand("Augustiner Bräu München") == "Augustiner"
    assert normalize_brand("Kaiser Bier") == "Kaiser"
    assert normalize_brand("Zillertaler") == "Zillertal Bier"
    assert normalize_brand("guiness") == "Guinness"


def test_underscores_become_spaces():
    assert normalize_brand("asahi_super_dry") == "Asahi Super Dry"
    assert normalize_brand("könig_ludwig") == "König Ludwig"


def test_lowercase_names_get_capitalized():
    assert normalize_brand("jever") == "Jever"
    assert normalize_brand("guinness") == "Guinness"
    assert normalize_brand("beck's") == "Beck's"


def test_mixed_case_and_whitespace_kept_as_is():
    # An unknown name with existing capitalization is trusted verbatim.
    assert normalize_brand("Maisel's Weisse") == "Maisel's Weisse"
    assert normalize_brand("ÜberQuell") == "ÜberQuell"
    assert normalize_brand("  Duckstein   Original ") == "Duckstein Original"
