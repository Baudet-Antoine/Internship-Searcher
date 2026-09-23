"""Étape prefilter : collected → prefiltered | rejected."""

from __future__ import annotations

from datetime import date

import psycopg

from stage_radar import db
from stage_radar.config import Country
from stage_radar.models import OfferStatus, RunReport
from stage_radar.rules import Rules, evaluate


def run_prefilter(conn: psycopg.Connection, rules: Rules, countries: dict[str, Country],
                  today: date, report: RunReport) -> None:
    for offer in db.fetch_offers(conn, [OfferStatus.COLLECTED]):
        verdict = evaluate(offer, rules, countries, today)
        if verdict.passed:
            db.update_offer(conn, offer["id"], status=OfferStatus.PREFILTERED)
            report.count("prefilter", "passed")
        else:
            db.reject(conn, offer["id"], "prefilter", verdict.reason)
            report.count("prefilter", verdict.code)
        conn.commit()
