from stage_radar.config import load_city_costs, load_countries, load_yaml


def test_countries_seed():
    countries = load_countries()
    assert countries["DE"].visa_lead_weeks == 0
    assert countries["US"].visa_lead_weeks == 12
    assert countries["US"].in_scope is True
    assert countries["FR"].in_scope is False


def test_city_costs_are_keyed_by_normalized_name():
    costs = load_city_costs()
    assert costs["london"] == 2000
    assert costs["munchen"] == 1400


def test_yaml_configs_load():
    assert load_yaml("rules.yaml")["max_age_days"] == 45
    questions = load_yaml("decisions.yaml")["questions"]
    convention = next(q for q in questions if q["id"] == "is_internship_convention")
    assert convention["reject_answers"] == ["no"]  # pas de booléen YAML
    assert abs(sum(load_yaml("scoring.yaml")["weights"].values()) - 1.0) < 1e-9
