"""Accès Postgres : migrations et requêtes de la machine à états."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from stage_radar.config import ROOT
from stage_radar.models import OfferStatus, RawOffer, RunReport

MIGRATIONS_DIR = ROOT / "supabase" / "migrations"

OFFER_COLUMNS = """
  id::text as id, title, company, country, city, description, description_is_full,
  posted_at, status::text as status, decisions, extracted, flags, score, score_breakdown,
  summary, notified_at, rejected_stage, rejected_reason
"""


def connect(url: str) -> psycopg.Connection:
    # prepare_threshold=None : compatible avec le pooler Supabase (pgbouncer)
    return psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    conn.execute(
        "create table if not exists schema_migrations "
        "(name text primary key, applied_at timestamptz not null default now())"
    )
    done = {r["name"] for r in conn.execute("select name from schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    conn.commit()
    return applied


def upsert_offer(conn: psycopg.Connection, raw: RawOffer, dedup: str) -> tuple[str, bool]:
    row = conn.execute(
        """
        insert into offers (dedup_key, title, company, country, city, description,
                            description_is_full, posted_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (dedup_key) do update set
          last_seen_at = now(),
          description = case when excluded.description_is_full and not offers.description_is_full
                             then excluded.description else offers.description end,
          description_is_full = offers.description_is_full or excluded.description_is_full
        returning id::text as id, (xmax = 0) as inserted
        """,
        (dedup, raw.title, raw.company, raw.country, raw.city, raw.description,
         raw.description_is_full, raw.posted_at),
    ).fetchone()
    conn.execute(
        """
        insert into offer_sources (offer_id, source, source_id, url, raw)
        values (%s, %s, %s, %s, %s)
        on conflict (source, source_id) do update set last_seen_at = now()
        """,
        (row["id"], raw.source, raw.source_id, raw.url, Jsonb(raw.raw)),
    )
    return row["id"], bool(row["inserted"])


def fetch_offers(conn: psycopg.Connection, statuses: list[OfferStatus]) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers where status = any(%s::offer_status[]) "
        "order by collected_at, id",
        ([str(s) for s in statuses],),
    ).fetchall()


def fetch_to_enrich(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status = 'classified' or (status = 'notified' and summary is null) "
        "order by collected_at, id"
    ).fetchall()


def fetch_unnotified(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status in ('classified', 'enriched') and notified_at is null "
        "order by collected_at, id"
    ).fetchall()


def fetch_scorable(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status in ('classified', 'enriched', 'notified') order by collected_at, id"
    ).fetchall()


def update_offer(conn: psycopg.Connection, offer_id: str, **fields: Any) -> None:
    assignments, values = [], []
    for key, value in fields.items():
        cast = "::offer_status" if key == "status" else ""
        assignments.append(f"{key} = %s{cast}")
        values.append(Jsonb(value) if isinstance(value, dict | list) else value)
    assignments.append("updated_at = now()")
    conn.execute(
        f"update offers set {', '.join(assignments)} where id = %s", (*values, offer_id)
    )


def reject(conn: psycopg.Connection, offer_id: str, stage: str, reason: str, **fields: Any) -> None:
    update_offer(conn, offer_id, status=OfferStatus.REJECTED, rejected_stage=stage,
                 rejected_reason=reason, **fields)


def mark_notified(conn: psycopg.Connection, ids: list[str]) -> None:
    if not ids:
        return
    conn.execute(
        "update offers set notified_at = now(), updated_at = now(), "
        "status = case when status in ('classified', 'enriched') then 'notified'::offer_status "
        "else status end where id = any(%s::uuid[])",
        (ids,),
    )


def fetch_sources(conn: psycopg.Connection, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = conn.execute(
        "select offer_id::text as offer_id, source, url from offer_sources "
        "where offer_id = any(%s::uuid[]) order by first_seen_at, source",
        (ids,),
    ).fetchall()
    for r in rows:
        out[r["offer_id"]].append({"source": r["source"], "url": r["url"]})
    return out


def rejected_sample(conn: psycopg.Connection, since: datetime, n: int) -> list[dict]:
    return conn.execute(
        "select title, company, country, rejected_stage, rejected_reason from offers "
        "where status = 'rejected' and updated_at >= %s order by random() limit %s",
        (since, n),
    ).fetchall()


def start_run(conn: psycopg.Connection) -> tuple[str, datetime]:
    row = conn.execute(
        "insert into runs default values returning id::text as id, started_at"
    ).fetchone()
    conn.commit()
    return row["id"], row["started_at"]


def finish_run(conn: psycopg.Connection, run_id: str, report: RunReport) -> None:
    conn.execute(
        "update runs set finished_at = now(), counts = %s, errors = %s where id = %s",
        (Jsonb(report.counts), Jsonb(report.errors), run_id),
    )


def last_run_start(conn: psycopg.Connection) -> datetime | None:
    row = conn.execute(
        "select started_at from runs where finished_at is not null "
        "order by started_at desc limit 1"
    ).fetchone()
    return row["started_at"] if row else None
