from pipeline.config import build_overpass_ql, normalize_brand


def test_overpass_ql_sweeps_cities_and_national_brewery_layer():
    ql = build_overpass_ql()
    assert '["name"="Hamburg"]["admin_level"="4"]' in ql
    assert '["name"="Leipzig"]["admin_level"="6"]' in ql
    assert '["name"="Berlin"]["admin_level"="4"]' in ql
    assert '["name"="München"]["admin_level"="6"]' in ql
    # Hannover's city boundary is admin_level 8 (level 6 is Region Hannover).
    assert '["name"="Hannover"]["admin_level"="8"]' in ql
    # Nationwide only brewery-tagged venues — a full-Germany amenity sweep
    # would be ~250k elements and times out on Overpass.
    assert '"brewery"](area.de)' in ql
    assert ql.count('nwr["amenity"') == 2
    # City names are not globally unique, so the sweep must stay inside DE.
    assert '(area.cities)(area.de)' in ql


def test_overpass_ql_takes_custom_sweep_areas():
    ql = build_overpass_ql(sweep_areas=('["name"="Dresden"]["admin_level"="6"]',))
    assert '"Dresden"' in ql and '"Hamburg"' not in ql


def test_alias_lookup_is_case_insensitive():
    assert normalize_brand("Budweiser") == "Budweiser Budvar"
    assert normalize_brand("budweiser budvar") == "Budweiser Budvar"
    assert normalize_brand("König Pilsener") == "König Pilsner"
    assert normalize_brand("königpilsener") == "König Pilsner"
    assert normalize_brand("Ratsherren") == "Ratsherrn"
    assert normalize_brand("Augustiner Bräu München") == "Augustiner"


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
