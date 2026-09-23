"""Score 0-100 pour le classement (jamais éliminatoire)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from stage_radar.config import Country
from stage_radar.normalize import normalize_text

BADGE_VALUE = {"profit": 1.0, "even": 0.7, "unknown": 0.5, "deficit": 0.2}
BALANCE_MARGIN = 200


def finance_badge(salary_eur_month: float | None, cost: int | None) -> str:
    if salary_eur_month is None or cost is None:
        return "unknown"
    balance = salary_eur_month - cost
    if balance > BALANCE_MARGIN:
        return "profit"
    if balance < -BALANCE_MARGIN:
        return "deficit"
    return "even"


def cost_of_living(city: str | None, country: str | None, countries: dict[str, Country],
                   city_costs: dict[str, int]) -> int | None:
    if city and normalize_text(city) in city_costs:
        return city_costs[normalize_text(city)]
    c = countries.get((country or "").upper())
    return c.cost_of_living_eur if c else None


@dataclass(frozen=True)
class ScoreInput:
    profile_fit: int | None
    posted_at: datetime | None
    deadline: date | None
    badge: str
    n_flags: int


def compute(inp: ScoreInput, cfg: dict, today: date) -> tuple[float, dict]:
    fit = ((inp.profile_fit or 3) - 1) / 4
    if inp.posted_at is None:
        freshness = 0.5
    else:
        age = max(0, (today - inp.posted_at.date()).days)
        freshness = max(0.0, 1 - age / cfg["freshness_days"])
    urgency = 0.0
    if inp.deadline is not None:
        days = (inp.deadline - today).days
        full, zero = cfg["urgency_full_days"], cfg["urgency_zero_days"]
        urgency = 1.0 if days <= full else max(0.0, 1 - (days - full) / (zero - full))
    parts = {
        "fit": fit,
        "freshness": freshness,
        "urgency": urgency,
        "finance": BADGE_VALUE[inp.badge],
        "certainty": max(0.0, 1 - 0.25 * inp.n_flags),
    }
    weights = cfg["weights"]
    score = 100 * sum(weights[k] * v for k, v in parts.items())
    return round(score, 1), {k: round(v, 3) for k, v in parts.items()}


def explain(inp: ScoreInput, parts: dict, country_code: str | None, today: date) -> str:
    reasons = []
    if inp.profile_fit and inp.profile_fit >= 4:
        reasons.append(f"profil {inp.profile_fit}/5")
    if inp.posted_at is not None:
        age = (today - inp.posted_at.date()).days
        if age <= 0:
            reasons.append("publiée aujourd'hui")
        elif age == 1:
            reasons.append("publiée hier")
        elif age <= 7:
            reasons.append(f"publiée il y a {age} j")
    if inp.deadline is not None and parts["urgency"] >= 0.5:
        reasons.append(f"fenêtre {country_code} se ferme dans {(inp.deadline - today).days} j")
    if inp.badge == "profit":
        reasons.append("salaire > coût de la vie")
    return " · ".join(reasons)
