"""Étape classify : prefiltered → classified | rejected, via un DecisionEngine."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from stage_radar import db
from stage_radar.collectors.base import SourceAuthError, redact
from stage_radar.engines.base import Decision, DecisionEngine, Question, offer_text
from stage_radar.engines.gemini import GeminiQuotaError
from stage_radar.models import OfferStatus, RunReport
from stage_radar.progress import Progress, log


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
                 report: RunReport) -> None:
    reject_threshold, uncertain_below = thresholds
    offers = db.fetch_offers(conn, [OfferStatus.PREFILTERED])
    progress = Progress("classify", total=len(offers))
    passed = 0
    for index, offer in enumerate(offers):
        title = offer["title"][:60]
        try:
            decisions = engine.classify(offer_text(offer), profile, questions)
        except (GeminiQuotaError, SourceAuthError) as exc:
            report.error("classify", redact(str(exc)))
            log.warning("classify : %s", redact(str(exc)))
            log.warning("classify : arrêt, %d offre(s) reprise(s) au prochain passage",
                        len(offers) - index)
            break
        except Exception as exc:  # une offre problématique ne bloque pas les suivantes
            report.error("classify", redact(f"{type(exc).__name__}: {exc}"))
            progress.step(f"erreur ({type(exc).__name__}) · {title}")
            continue
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
            report.count("classify", "passed")
            passed += 1
            verdict = "retenue" + (" (à vérifier)" if outcome.flags else "")
        conn.commit()
        progress.step(f"{verdict} · {title}")
    progress.done(f"{passed} retenue(s) sur {progress.count} traitée(s)")
