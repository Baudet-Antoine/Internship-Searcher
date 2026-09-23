"""Extraction de salaire par regex, conversion en EUR mensuel, plausibilité."""

from __future__ import annotations

import re

import httpx

FX_URL = "https://api.frankfurter.app/latest"
FALLBACK_RATES = {"GBP": 0.85, "CHF": 0.94, "USD": 1.10, "CAD": 1.50, "AUD": 1.65,
                  "SGD": 1.45, "SEK": 11.3, "DKK": 7.46, "NOK": 11.6, "PLN": 4.3, "CZK": 25.0}
MIN_EUR, MAX_EUR = 300, 6000
HOURS_PER_MONTH = 160

_CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP",
               "gbp": "GBP", "chf": "CHF", "$": "USD", "usd": "USD", "sek": "SEK",
               "dkk": "DKK", "nok": "NOK", "pln": "PLN"}
_CUR = r"€|£|\$|\b(?:eur|euros?|gbp|chf|usd|sek|dkk|nok|pln)\b"
_PATTERN = re.compile(
    rf"(?P<c1>{_CUR})?\s*"
    r"(?P<num>\d{1,3}(?:[.,  ]\d{3})+|\d{1,6})(?:[.,]\d{1,2})?\s*"
    rf"(?P<c2>{_CUR})?\s*"
    r"(?:brutto|gross|bruts?|netto|net)?\s*(?:/|per|pro|par|a|an|al|each)?\s*"
    r"(?P<period>month|monat|mois|mes|mese|year|jahr|annum|hour|stunde|heure)\w*",
    re.IGNORECASE,
)


def _period(word: str) -> str:
    word = word.lower()
    if word.startswith(("year", "jahr", "annum")):
        return "year"
    if word.startswith(("hour", "stunde", "heure")):
        return "hour"
    return "month"


def find_salary(text: str) -> tuple[float, str, str] | None:
    for match in _PATTERN.finditer(text or ""):
        currency = match.group("c1") or match.group("c2")
        if not currency:
            continue
        amount = float(re.sub(r"[.,  ]", "", match.group("num")))
        return amount, _CURRENCIES[currency.lower()], _period(match.group("period"))
    return None


def to_monthly_eur(amount: float, currency: str, period: str,
                   rates: dict[str, float]) -> float | None:
    monthly = {"month": amount, "year": amount / 12, "hour": amount * HOURS_PER_MONTH}[period]
    currency = currency.upper()
    if currency == "EUR":
        return monthly
    rate = rates.get(currency)
    return monthly / rate if rate else None


def plausible(eur_per_month: float) -> bool:
    return MIN_EUR <= eur_per_month <= MAX_EUR


def fetch_rates(client: httpx.Client | None = None) -> dict[str, float]:
    """Taux BCE (1 EUR = x devise) via Frankfurter ; valeurs de secours si indisponible."""
    try:
        response = (client or httpx.Client(timeout=20)).get(FX_URL, params={"from": "EUR"})
        response.raise_for_status()
        return {**FALLBACK_RATES, **response.json()["rates"]}
    except (httpx.HTTPError, KeyError, ValueError):
        return dict(FALLBACK_RATES)
