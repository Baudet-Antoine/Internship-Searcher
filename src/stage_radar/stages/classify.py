"""Étape classify : prefiltered → classified | rejected, via un DecisionEngine.

Si le moteur sait aussi extraire (méthode classify_and_extract, ex. Gemini), un seul appel
par offre suffit : l'offre retenue passe directement à enriched. Le facteur limitant du
palier gratuit est le nombre de requêtes, pas les tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from stage_radar import db
from stage_radar.collectors.base import redact
from stage_radar.engines.base import Decision, DecisionEngine, Question, offer_text
from stage_radar.extraction import finalize
from stage_radar.models import OfferStatus, RunReport
from stage_radar.progress import Progress
from stage_radar.stages.common import FATAL_ERRORS, MAX_CONSECUTIVE_ERRORS, stop_stage


@dataclass
class Outcome:
    rejected: bool
    code: str | None = None
    reason: str | None = None
    flags: list[str] = field(default_factory=list)


def apply_zones(questions: list[Question], decisions: dict[str, Decision],
                reject_threshold: float, uncertain_below: float) -> Outcome:
    flags: list[str] = []
    for q in questions:
        d = decisions.get(q.id)
        if d is None:
            flags.append(f"{q.label} : pas de réponse")
        elif d.answer in q.reject_answers:
            if d.p >= reject_threshold:
                return Outcome(True, q.id, f"{q.label} = {d.answer} (p={d.p:.2f})", flags)
            flags.append(f"{q.label} : {d.answer} ? (p={d.p:.2f})")
        elif d.answer in q.flag_answers:
            flags.append(q.flag_text or f"{q.label} non précisé")
        elif q.reject_answers and d.p < uncertain_below:
            flags.append(f"{q.label} incertain")
    return Outcome(False, flags=flags)


def run_classify(conn: psycopg.Connection, engine: DecisionEngine, questions: list[Question],
                 profile: str, thresholds: tuple[float, float], version: str,
                 report: RunReport, rates: dict[str, float] | None = None) -> None:
    reject_threshold, uncertain_below = thresholds
    combined = getattr(engine, "classify_and_extract", None) if rates is not None else None
    offers = db.fetch_offers(conn, [OfferStatus.PREFILTERED])
    progress = Progress("classify", total=len(offers))
    passed = consecutive_errors = 0
    for index, offer in enumerate(offers):
        title = offer["title"][:60]
        try:
            if combined:
                decisions, extraction = combined(offer_text(offer), profile, questions)
            else:
                decisions, extraction = engine.classify(offer_text(offer), profile,
                                                        questions), None
        except FATAL_ERRORS as exc:
            report.error("classify", redact(str(exc)))
            stop_stage("classify", len(offers) - index, redact(str(exc)))
            break
        except Exception as exc:  # une offre problématique ne bloque pas les suivantes
            message = redact(f"{type(exc).__name__}: {exc}")
            report.error("classify", message)
            progress.step(f"erreur · {title} · {message}")
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                stop_stage("classify", len(offers) - index - 1,
                           f"{MAX_CONSECUTIVE_ERRORS} erreurs consécutives, erreur probablement "
                           f"systématique — {message}")
                break
            continue
        consecutive_errors = 0
        outcome = apply_zones(questions, decisions, reject_threshold, uncertain_below)
        payload = {k: {"answer": d.answer, "p": d.p} for k, d in decisions.items()}
        if outcome.rejected:
            db.reject(conn, offer["id"], "classify", outcome.reason, decisions=payload,
                      engine_version=version)
            report.count("classify", f"rejected_{outcome.code}")
            verdict = f"rejetée ({outcome.code})"
        else:
            db.update_offer(conn, offer["id"], status=OfferStatus.CLASSIFIED, decisions=payload,
                            flags=outcome.flags, engine_version=version)
            if extraction is not None:
                extracted, summary = finalize(extraction, offer["description"], rates or {})
                db.update_offer(conn, offer["id"], status=OfferStatus.ENRICHED,
                                extracted=extracted, summary=summary,
                                city=offer["city"] or extracted["city"])
            report.count("classify", "passed")
            passed += 1
            verdict = "retenue" + (" (à vérifier)" if outcome.flags else "")
        conn.commit()
        progress.step(f"{verdict} · {title}")
    progress.done(f"{passed} retenue(s) sur {progress.count} traitée(s)")
