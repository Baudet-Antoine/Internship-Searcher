"""Étape enrich : extraction structurée (Gemini) pour les offres classées sans extraction.

Quand le moteur de décision sait aussi extraire (Gemini), classify fait tout en un appel et
cette étape n'a rien à traiter. Elle sert aux moteurs qui ne font que décider (ex. Laya).
"""

from __future__ import annotations

from typing import Protocol

import psycopg

from stage_radar import db
from stage_radar.collectors.base import redact
from stage_radar.engines.base import offer_text
from stage_radar.extraction import EXTRACTION_SCHEMA, EXTRACTION_SYSTEM, Extraction, finalize
from stage_radar.models import OfferStatus, RunReport
from stage_radar.progress import Progress
from stage_radar.stages.common import FATAL_ERRORS, MAX_CONSECUTIVE_ERRORS, stop_stage

__all__ = ["Extraction", "JsonClient", "finalize", "run_enrich"]


class JsonClient(Protocol):
    def generate_json(self, system: str, prompt: str, schema: dict) -> dict: ...


def run_enrich(conn: psycopg.Connection, client: JsonClient, rates: dict[str, float],
               report: RunReport) -> None:
    offers = db.fetch_to_enrich(conn)
    progress = Progress("enrich", total=len(offers))
    enriched = consecutive_errors = 0
    for index, offer in enumerate(offers):
        title = offer["title"][:60]
        try:
            data = client.generate_json(EXTRACTION_SYSTEM, offer_text(offer), EXTRACTION_SCHEMA)
            extraction = Extraction.model_validate(data)
        except FATAL_ERRORS as exc:
            report.error("enrich", redact(str(exc)))
            stop_stage("enrich", len(offers) - index, redact(str(exc)))
            break
        except Exception as exc:  # réponse invalide : on passe à l'offre suivante
            message = redact(f"{type(exc).__name__}: {exc}")
            report.error("enrich", message)
            progress.step(f"erreur · {title} · {message}")
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                stop_stage("enrich", len(offers) - index - 1,
                           f"{MAX_CONSECUTIVE_ERRORS} erreurs consécutives, erreur probablement "
                           f"systématique — {message}")
                break
            continue
        consecutive_errors = 0
        extracted, summary = finalize(extraction, offer["description"], rates)
        fields = {"extracted": extracted, "summary": summary,
                  "city": offer["city"] or extracted["city"]}
        if offer["status"] == OfferStatus.CLASSIFIED:
            fields["status"] = OfferStatus.ENRICHED
        db.update_offer(conn, offer["id"], **fields)
        conn.commit()
        report.count("enrich", "enriched")
        enriched += 1
        salary = extracted["salary_eur_month"]
        progress.step((f"{salary} €/mois" if salary is not None else "salaire inconnu")
                      + f" · {title}")
    progress.done(f"{enriched} enrichie(s)")
