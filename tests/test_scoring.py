from datetime import UTC, date, datetime

from stage_radar.config import load_city_costs, load_countries, load_yaml
from stage_radar.scoring import ScoreInput, compute, cost_of_living, explain, finance_badge

CFG = load_yaml("scoring.yaml")
TODAY = date(2026, 10, 1)


def test_finance_badge():
    assert finance_badge(None, 1400) == "unknown"
    assert finance_badge(1700, 1400) == "profit"
    assert finance_badge(1500, 1400) == "even"
    assert finance_badge(900, 1400) == "deficit"
    assert finance_badge(1500, None) == "unknown"


def test_cost_of_living_prefers_city():
    countries, cities = load_countries(), load_city_costs()
    assert cost_of_living("London", "GB", countries, cities) == 2000
    assert cost_of_living("Leeds", "GB", countries, cities) == 1500
    assert cost_of_living(None, "XX", countries, cities) is None


def test_compute_perfect_offer_scores_100():
    inp = ScoreInput(profile_fit=5, posted_at=datetime(2026, 10, 1, tzinfo=UTC),
                     deadline=date(2026, 10, 15), badge="profit", n_flags=0)
    score, parts = compute(inp, CFG, TODAY)
    assert score == 100.0 and parts["urgency"] == 1.0


def test_compute_components():
    inp = ScoreInput(profile_fit=3, posted_at=datetime(2026, 9, 24, tzinfo=UTC),
                     deadline=date(2027, 2, 1), badge="unknown", n_flags=2)
    score, parts = compute(inp, CFG, TODAY)
    assert parts == {"fit": 0.5, "freshness": 0.5, "urgency": 0.0, "finance": 0.5,
                     "certainty": 0.5}
    assert score == 42.5


def test_explain():
    inp = ScoreInput(5, datetime(2026, 9, 30, tzinfo=UTC), date(2026, 10, 19), "profit", 0)
    _, parts = compute(inp, CFG, TODAY)
    text = explain(inp, parts, "GB", TODAY)
    assert text == "profil 5/5 · publiée hier · fenêtre GB se ferme dans 18 j · salaire > coût de la vie"
