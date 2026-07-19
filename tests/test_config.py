from pipeline.config import normalize_brand


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
