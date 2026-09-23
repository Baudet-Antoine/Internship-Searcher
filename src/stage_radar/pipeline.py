"""Assemble les étapes : collect → prefilter → classify → enrich → notify."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml

from stage_radar import db
from stage_radar.collectors import build_collectors
from stage_radar.collectors.base import Collector, redact
from stage_radar.config import Country, load_city_costs, load_countries, load_yaml
from stage_radar.emailer import FileSender, ResendSender, Sender
from stage_radar.engines.base import DecisionEngine, Question, engine_version, load_questions
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import GeminiClient, GeminiEngine
from stage_radar.models import RunReport
from stage_radar.rules import Rules
from stage_radar.salary import fetch_rates
from stage_radar.stages.classify import run_classify
from stage_radar.stages.collect import run_collect
from stage_radar.stages.enrich import JsonClient, run_enrich
from stage_radar.stages.notify import NotifyContext, run_notify
from stage_radar.stages.prefilter import run_prefilter

STAGES = ["collect", "prefilter", "classify", "enrich", "notify"]


@dataclass
class Settings:
    search: dict
    decisions: dict
    scoring: dict
    rules: Rules
    questions: list[Question]
    profile: str
    countries: dict[str, Country]
    city_costs: dict[str, int]


def load_settings() -> Settings:
    decisions = load_yaml("decisions.yaml")
    return Settings(
        search=load_yaml("search.yaml"),
        decisions=decisions,
        scoring=load_yaml("scoring.yaml"),
        rules=Rules.from_config(load_yaml("rules.yaml")),
        questions=load_questions(decisions),
        profile=yaml.safe_dump(load_yaml("profile.yaml"), allow_unicode=True, sort_keys=False),
        countries=load_countries(),
        city_costs=load_city_costs(),
    )


@dataclass
class Components:
    collectors: list[Collector]
    engine: DecisionEngine | None
    llm: JsonClient | None
    sender: Sender
    rates_loader: Callable[[], dict[str, float]]


def build_components(settings: Settings, env: Mapping[str, str],
                     dry_run_dir: Path | None = None) -> Components:
    llm = None
    if env.get("GEMINI_API_KEY"):
        llm = GeminiClient(env["GEMINI_API_KEY"], settings.decisions.get("gemini_model",
                                                                         "gemini-2.5-flash"),
                           min_interval_s=float(settings.decisions.get("gemini_min_interval_s",
                                                                       0)))
    engine: DecisionEngine | None
    if settings.decisions.get("engine") == "fake":
        engine = FakeEngine()
    else:
        engine = GeminiEngine(llm) if llm else None
    if dry_run_dir is not None:
        sender: Sender = FileSender(dry_run_dir)
    else:
        sender = ResendSender(env.get("RESEND_API_KEY", ""), env.get("DIGEST_TO", ""))
    return Components(collectors=build_collectors(settings.search, env), engine=engine,
                      llm=llm, sender=sender, rates_loader=fetch_rates)


def _since(conn: psycopg.Connection, settings: Settings, today: date) -> date:
    last = db.last_run_start(conn)
    if last is None:
        return today - timedelta(days=settings.search.get("lookback_days_first_run", 30))
    return last.date() - timedelta(days=settings.search.get("overlap_days", 2))


def run_pipeline(conn: psycopg.Connection, comps: Components, settings: Settings,
                 stages: list[str], today: date) -> int:
    report = RunReport()
    since = _since(conn, settings, today)
    run_id, started = db.start_run(conn)
    crashed = False
    version = engine_version(comps.engine.name if comps.engine else "none", settings.decisions)
    thresholds = (settings.decisions.get("reject_threshold", 0.85),
                  settings.decisions.get("uncertain_below", 0.6))

    steps: dict[str, Callable[[], None]] = {
        "collect": lambda: run_collect(conn, comps.collectors, since, report),
        "prefilter": lambda: run_prefilter(conn, settings.rules, settings.countries, today,
                                           report),
        "classify": lambda: (
            run_classify(conn, comps.engine, settings.questions, settings.profile, thresholds,
                         version, report)
            if comps.engine else report.error("classify", "GEMINI_API_KEY manquant")
        ),
        "enrich": lambda: (
            run_enrich(conn, comps.llm, comps.rates_loader(), report)
            if comps.llm else report.error("enrich", "GEMINI_API_KEY manquant")
        ),
        "notify": lambda: run_notify(
            conn, comps.sender,
            NotifyContext(settings.countries, settings.city_costs, settings.rules,
                          settings.scoring),
            today, started, report),
    }
    for name in stages:
        try:
            steps[name]()
        except Exception as exc:
            conn.rollback()
            crashed = True
            report.error(name, redact(f"{type(exc).__name__}: {exc}"))

    db.finish_run(conn, run_id, report)
    conn.commit()
    names = [c.name for c in comps.collectors]
    all_sources_failed = "collect" in stages and names and all(n in report.errors for n in names)
    return 1 if crashed or all_sources_failed else 0
