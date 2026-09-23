"""Étape collect : sources → offers (statut collected)."""

from __future__ import annotations

from datetime import date

import psycopg

from stage_radar import db
from stage_radar.collectors.base import Collector, redact
from stage_radar.models import RunReport
from stage_radar.normalize import dedup_key


def run_collect(conn: psycopg.Connection, collectors: list[Collector], since: date,
                report: RunReport) -> None:
    for collector in collectors:
        seen = new = 0
        try:
            for raw in collector.fetch(since):
                if not raw.title:
                    continue
                _, is_new = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title,
                                                                 raw.country))
                conn.commit()
                seen += 1
                new += int(is_new)
        except Exception as exc:  # une source en panne ne bloque pas les autres
            conn.rollback()
            report.error(collector.name, redact(f"{type(exc).__name__}: {exc}"))
        report.count("collect", f"{collector.name}_seen", seen)
        report.count("collect", f"{collector.name}_new", new)
