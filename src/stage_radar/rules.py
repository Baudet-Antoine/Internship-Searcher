"""Étage A : règles déterministes appliquées avant tout modèle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from stage_radar.config import Country
from stage_radar.normalize import normalize_title
from stage_radar.visa import apply_deadline


@dataclass(frozen=True)
class Rules:
    latest_start: date
    recruitment_weeks: int
    max_age_days: int
    excluded_countries: frozenset[str]
    data: tuple[re.Pattern, ...]
    internship: tuple[re.Pattern, ...]
    blacklist: tuple[re.Pattern, ...]

    @classmethod
    def from_config(cls, cfg: dict) -> Rules:
        def compile_all(key: str) -> tuple[re.Pattern, ...]:
            return tuple(re.compile(p) for p in cfg.get(key, []))

        return cls(
            latest_start=cfg["latest_start"],
            recruitment_weeks=int(cfg["recruitment_weeks"]),
            max_age_days=int(cfg["max_age_days"]),
            excluded_countries=frozenset(c.upper() for c in cfg.get("excluded_countries", [])),
            data=compile_all("title_data_patterns"),
            internship=compile_all("title_internship_patterns"),
            blacklist=compile_all("title_blacklist_patterns"),
        )


@dataclass(frozen=True)
class Verdict:
    passed: bool
    code: str | None = None
    reason: str | None = None


def _reject(code: str, reason: str) -> Verdict:
    return Verdict(False, code, reason)


def evaluate(offer: dict, rules: Rules, countries: dict[str, Country], today: date) -> Verdict:
    code = (offer.get("country") or "").upper()
    if not code:
        return _reject("country", "pays inconnu")
    if code in rules.excluded_countries:
        return _reject("country", f"pays exclu ({code})")
    country = countries.get(code)
    if country is None or not country.in_scope:
        return _reject("country", f"pays hors périmètre ({code})")

    title = normalize_title(offer.get("title"))
    if not any(p.search(title) for p in rules.data):
        return _reject("title_data", "titre sans terme data")
    if not any(p.search(title) for p in rules.internship):
        return _reject("title_internship", "titre sans terme stage")
    for pattern in rules.blacklist:
        match = pattern.search(title)
        if match:
            return _reject("title_blacklist", f"titre exclu ({match.group(0)})")

    posted = offer.get("posted_at")
    if posted is not None:
        age = (today - posted.date()).days
        if age > rules.max_age_days:
            return _reject("too_old", f"offre trop ancienne ({age} j)")

    deadline = apply_deadline(country, rules.latest_start, rules.recruitment_weeks)
    if today > deadline:
        return _reject("visa_window",
                       f"fenêtre visa fermée ({code}, limite {deadline.isoformat()})")
    return Verdict(True)
