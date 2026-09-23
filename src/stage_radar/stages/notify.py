"""Étape notify : score, construit et envoie le digest, marque les offres notifiées."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg

from stage_radar import db
from stage_radar.config import Country
from stage_radar.digest import (
    Digest,
    DigestItem,
    flag_emoji,
    french_date,
    one_line,
    render,
    stats_lines,
)
from stage_radar.emailer import Sender
from stage_radar.models import RunReport
from stage_radar.rules import Rules
from stage_radar.scoring import ScoreInput, compute, cost_of_living, explain, finance_badge
from stage_radar.visa import apply_deadline

BADGE_ICON = {"profit": "🟢", "even": "🟡", "deficit": "🔴", "unknown": "⚪"}
LABELS = {"onsite": "sur site", "hybrid": "hybride", "remote": "full remote",
          "6plus": "6 mois +", "dec_mar": "déc.-mars"}
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0}


@dataclass(frozen=True)
class NotifyContext:
    countries: dict[str, Country]
    city_costs: dict[str, int]
    rules: Rules
    scoring_cfg: dict


def _deadline(ctx: NotifyContext, country: str | None) -> date | None:
    c = ctx.countries.get((country or "").upper())
    if c is None:
        return None
    return apply_deadline(c, ctx.rules.latest_start, ctx.rules.recruitment_weeks)


def _score(offer: dict, ctx: NotifyContext,
           today: date) -> tuple[ScoreInput, float, dict, int | None]:
    fit_answer = (offer["decisions"].get("profile_fit") or {}).get("answer")
    salary = offer["extracted"].get("salary_eur_month")
    cost = cost_of_living(offer["city"], offer["country"], ctx.countries, ctx.city_costs)
    inp = ScoreInput(
        profile_fit=int(fit_answer) if fit_answer and str(fit_answer).isdigit() else None,
        posted_at=offer["posted_at"],
        deadline=_deadline(ctx, offer["country"]),
        badge=finance_badge(salary, cost),
        n_flags=len(offer["flags"]),
    )
    score, parts = compute(inp, ctx.scoring_cfg, today)
    balance = round(salary - cost) if salary is not None and cost is not None else None
    return inp, score, parts, balance


def _meta(offer: dict, inp: ScoreInput, balance: int | None) -> str:
    parts = [BADGE_ICON[inp.badge] + (f" {balance:+d} €/mois" if balance is not None
                                      else " salaire inconnu")]
    for key in ("work_mode", "duration", "start"):
        answer = (offer["decisions"].get(key) or {}).get("answer")
        if answer in LABELS:
            parts.append(LABELS[answer])
    parts += [f"⚠️ {f}" for f in offer["flags"]]
    return " · ".join(parts)


def run_notify(conn: psycopg.Connection, sender: Sender, ctx: NotifyContext, today: date,
               run_started: datetime, report: RunReport) -> None:
    for offer in db.fetch_scorable(conn):
        _, score, parts, _ = _score(offer, ctx, today)
        db.update_offer(conn, offer["id"], score=score, score_breakdown=parts)
    conn.commit()

    pending = db.fetch_unnotified(conn)
    ranked = sorted(pending, key=lambda o: o["score"] or 0, reverse=True)
    top_n = ctx.scoring_cfg.get("top_n", 15)
    sources = db.fetch_sources(conn, [o["id"] for o in ranked[:top_n]])
    items = []
    for rank, offer in enumerate(ranked[:top_n], start=1):
        inp, score, parts, balance = _score(offer, ctx, today)
        links = sorted(sources.get(offer["id"], []),
                       key=lambda s: SOURCE_PRIORITY.get(s["source"], 1))
        items.append(DigestItem(
            rank=rank, title=offer["title"], company=offer["company"] or "?",
            flag=flag_emoji(offer["country"]),
            place=offer["city"] or offer["country"] or "",
            score=score, meta=_meta(offer, inp, balance), summary=offer["summary"] or "",
            snippet=one_line(offer["description"], 280),
            requirements=offer["extracted"].get("key_requirements") or [],
            why=explain(inp, parts, offer["country"], today), links=links,
        ))

    window = ctx.scoring_cfg.get("closing_window_days", 30)
    closing = []
    for c in sorted(ctx.countries.values(), key=lambda c: c.code):
        deadline = _deadline(ctx, c.code)
        if c.in_scope and deadline and today <= deadline <= today + timedelta(days=window):
            closing.append({"flag": flag_emoji(c.code), "name": c.name,
                            "deadline": deadline.strftime("%d/%m"),
                            "days": (deadline - today).days})

    audit = [{"title": r["title"], "company": r["company"] or "?",
              "reason": r["rejected_reason"]}
             for r in db.rejected_sample(conn, run_started, 5)]
    digest = Digest(date_label=french_date(today), new_count=len(ranked), items=items,
                    extra_count=max(0, len(ranked) - top_n), stats=stats_lines(report),
                    closing=closing, audit=audit, errors=dict(report.errors))
    sender.send(*render(digest))
    db.mark_notified(conn, [o["id"] for o in ranked])
    conn.commit()
    report.count("notify", "sent", len(ranked))
