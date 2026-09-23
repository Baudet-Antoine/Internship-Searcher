"""Étape prefilter : collected → prefiltered | rejected."""

from __future__ import annotations

from datetime import date

import psycopg

from stage_radar import db
from stage_radar.config import Country
from stage_radar.models import OfferStatus, RunReport
from stage_radar.progress import Progress
from stage_radar.rules import Rules, evaluate

LOG_EVERY = 100


def run_prefilter(conn: psycopg.Connection, rules: Rules, countries: dict[str, Country],
                  today: date, report: RunReport) -> None:
    offers = db.fetch_offers(conn, [OfferStatus.COLLECTED])
    progress = Progress("prefilter", total=len(offers), every=LOG_EVERY)
    passed = 0
    for offer in offers:
        verdict = evaluate(offer, rules, countries, today)
        if verdict.passed:
            db.update_offer(conn, offer["id"], status=OfferStatus.PREFILTERED)
            report.count("prefilter", "passed")
            passed += 1
        else:
            db.reject(conn, offer["id"], "prefilter", verdict.reason)
            report.count("prefilter", verdict.code)
        conn.commit()
        progress.step(f"{passed} retenues")
    progress.done(f"{passed} retenue(s) sur {len(offers)}")
