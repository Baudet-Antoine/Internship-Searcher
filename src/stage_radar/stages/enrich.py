"""Étape enrich : extraction structurée (Gemini) + garde-fous."""

from __future__ import annotations

import re
from typing import Literal, Protocol

import psycopg
from pydantic import BaseModel

from stage_radar import db
from stage_radar.collectors.base import SourceAuthError, redact
from stage_radar.engines.base import offer_text
from stage_radar.engines.gemini import GeminiQuotaError
from stage_radar.models import OfferStatus, RunReport
from stage_radar.salary import find_salary, plausible, to_monthly_eur

START_MIN, START_MAX = "2026-10", "2027-09"


class Extraction(BaseModel):
    salary_amount: float | None = None
    salary_currency: str | None = None
    salary_period: Literal["month", "year", "hour"] | None = None
    start_date: str | None = None
    duration_months: int | None = None
    city: str | None = None
    summary_fr: str = ""
    key_requirements: list[str] = []


def _nullable(kind: str, **extra) -> dict:
    return {"type": kind, "nullable": True, **extra}


EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "salary_amount": _nullable("NUMBER"),
        "salary_currency": _nullable("STRING"),
        "salary_period": _nullable("STRING", enum=["month", "year", "hour"]),
        "start_date": _nullable("STRING"),
        "duration_months": _nullable("INTEGER"),
        "city": _nullable("STRING"),
        "summary_fr": {"type": "STRING"},
        "key_requirements": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary_fr", "key_requirements"],
}

SYSTEM = (
    "Extract facts from an internship offer. Use null for anything not explicitly written in "
    "the offer; never guess. salary_currency is an ISO code (EUR, GBP, CHF...). start_date is "
    "YYYY-MM. summary_fr: two short sentences in French describing the mission and the tech "
    "stack. key_requirements: at most 5 short skills."
)


class JsonClient(Protocol):
    def generate_json(self, system: str, prompt: str, schema: dict) -> dict: ...


def finalize(ex: Extraction, description: str, rates: dict[str, float]) -> tuple[dict, str]:
    warnings: list[str] = []
    amount, currency, period, source = (ex.salary_amount, ex.salary_currency,
                                        ex.salary_period, "llm")
    if amount is None:
        found = find_salary(description)
        if found:
            amount, currency, period = found
            source = "regex"
    eur = None
    if amount is not None:
        eur = to_monthly_eur(amount, currency or "EUR", period or "month", rates)
        if eur is not None and not plausible(eur):
            warnings.append(f"salaire implausible ignoré ({amount} {currency}/{period})")
            eur = None

    start = ex.start_date
    if start is not None and not (re.fullmatch(r"\d{4}-\d{2}", start)
                                  and START_MIN <= start <= START_MAX):
        warnings.append(f"date de début hors fenêtre ignorée ({start})")
        start = None
    duration = ex.duration_months
    if duration is not None and not 1 <= duration <= 24:
        warnings.append(f"durée implausible ignorée ({duration})")
        duration = None

    extracted = {
        "salary_eur_month": round(eur) if eur is not None else None,
        "salary_source": source if eur is not None else None,
        "start_date": start,
        "duration_months": duration,
        "city": ex.city,
        "key_requirements": ex.key_requirements[:5],
        "warnings": warnings,
    }
    return extracted, ex.summary_fr.strip()


def run_enrich(conn: psycopg.Connection, client: JsonClient, rates: dict[str, float],
               report: RunReport) -> None:
    for offer in db.fetch_to_enrich(conn):
        try:
            data = client.generate_json(SYSTEM, offer_text(offer), EXTRACTION_SCHEMA)
            extraction = Extraction.model_validate(data)
        except (GeminiQuotaError, SourceAuthError) as exc:
            report.error("enrich", redact(str(exc)))
            break
        except Exception as exc:  # réponse invalide : on passe à l'offre suivante
            report.error("enrich", redact(f"{type(exc).__name__}: {exc}"))
            continue
        extracted, summary = finalize(extraction, offer["description"], rates)
        fields = {"extracted": extracted, "summary": summary,
                  "city": offer["city"] or extracted["city"]}
        if offer["status"] == OfferStatus.CLASSIFIED:
            fields["status"] = OfferStatus.ENRICHED
        db.update_offer(conn, offer["id"], **fields)
        conn.commit()
        report.count("enrich", "enriched")
