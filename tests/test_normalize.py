from stage_radar.normalize import (
    dedup_key,
    normalize_company,
    normalize_title,
    parse_dt,
    strip_html,
)


def test_normalize_title_removes_gender_tags_and_accents():
    assert normalize_title("Data Scientist Intern (m/w/d)") == "data scientist intern"
    assert normalize_title("Praktikum KI (f/m/x)") == "praktikum ki"
    assert normalize_title("Stage Données (all genders)") == "stage donnees"


def test_normalize_company_strips_legal_suffixes():
    assert normalize_company("Adyen N.V.") == "adyen"
    assert normalize_company("Zalando SE") == "zalando"
    assert normalize_company("Siemens AG") == "siemens"
    assert normalize_company(None) == ""


def test_dedup_key_is_stable_across_sources():
    a = dedup_key("Adyen N.V.", "Data Science Intern (m/f/d)", "NL")
    b = dedup_key("adyen", "Data Science Intern", "nl")
    assert a == b
    assert a != dedup_key("adyen", "Data Science Intern", "DE")


def test_strip_html():
    assert strip_html("<strong>Data</strong> &amp; AI") == "Data & AI"


def test_parse_dt():
    assert parse_dt("2026-09-20T10:00:00Z").tzinfo is not None
    assert parse_dt("2026-09-20").day == 20
    assert parse_dt("2026-09-20").tzinfo is not None
    assert parse_dt("pas une date") is None
    assert parse_dt(None) is None
