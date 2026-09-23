from datetime import UTC, datetime

from stage_radar import db
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key


def make_raw(source="adzuna", source_id="1", company="Adyen", title="Data Science Intern",
             country="NL", description="snippet", full=False):
    return RawOffer(
        source=source, source_id=source_id, url=f"https://example.com/{source}/{source_id}",
        title=title, company=company, location_raw="Amsterdam", country=country,
        city="Amsterdam", description=description, description_is_full=full,
        posted_at=datetime(2026, 9, 20, tzinfo=UTC), raw={"id": source_id},
    )


def insert(conn, raw):
    result = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
    conn.commit()
    return result


def test_upsert_is_idempotent_per_source(conn):
    offer_id, is_new = insert(conn, make_raw())
    again_id, again_new = insert(conn, make_raw())
    assert is_new is True and again_new is False and again_id == offer_id
    assert conn.execute("select count(*) as n from offers").fetchone()["n"] == 1


def test_cross_source_duplicates_share_one_offer(conn):
    a, _ = insert(conn, make_raw(source="adzuna", source_id="1", company="Adyen N.V."))
    b, is_new = insert(conn, make_raw(source="greenhouse", source_id="9", company="adyen"))
    assert a == b and is_new is False
    assert db.fetch_sources(conn, [a])[a] == [
        {"source": "adzuna", "url": "https://example.com/adzuna/1"},
        {"source": "greenhouse", "url": "https://example.com/greenhouse/9"},
    ]


def test_full_description_replaces_snippet(conn):
    offer_id, _ = insert(conn, make_raw(description="short"))
    insert(conn, make_raw(source="ats", source_id="2", description="long text", full=True))
    [offer] = db.fetch_offers(conn, [OfferStatus.COLLECTED])
    assert offer["description"] == "long text" and offer["description_is_full"] is True


def test_classified_status_creates_application(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.CLASSIFIED, flags=["durée non précisée"],
                    decisions={"duration": {"answer": "unspecified", "p": 0.9}})
    conn.commit()
    app = conn.execute("select user_status from applications where offer_id = %s",
                       (offer_id,)).fetchone()
    assert app["user_status"] == "new"
    [offer] = db.fetch_offers(conn, [OfferStatus.CLASSIFIED])
    assert offer["flags"] == ["durée non précisée"]
    assert offer["decisions"]["duration"]["p"] == 0.9


def test_reject_and_sample(conn):
    offer_id, _ = insert(conn, make_raw())
    started = conn.execute("select now() - interval '1 minute' as t").fetchone()["t"]
    db.reject(conn, offer_id, "prefilter", "titre sans terme stage")
    conn.commit()
    [row] = db.rejected_sample(conn, started, 5)
    assert row["rejected_reason"] == "titre sans terme stage"
    assert row["title"] == "Data Science Intern"


def test_unnotified_and_mark_notified(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.ENRICHED)
    conn.commit()
    assert [o["id"] for o in db.fetch_unnotified(conn)] == [offer_id]
    db.mark_notified(conn, [offer_id])
    conn.commit()
    assert db.fetch_unnotified(conn) == []
    assert [o["id"] for o in db.fetch_scorable(conn)] == [offer_id]


def test_fetch_to_enrich_includes_notified_without_summary(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.CLASSIFIED)
    db.mark_notified(conn, [offer_id])
    conn.commit()
    assert [o["id"] for o in db.fetch_to_enrich(conn)] == [offer_id]


def test_runs_journal(conn):
    assert db.last_run_start(conn) is None
    run_id, started = db.start_run(conn)
    report = RunReport()
    report.count("collect", "adzuna_seen", 3)
    report.error("adzuna", "boom")
    db.finish_run(conn, run_id, report)
    conn.commit()
    assert db.last_run_start(conn) == started
    row = conn.execute("select counts, errors from runs").fetchone()
    assert row["counts"] == {"collect": {"adzuna_seen": 3}}
    assert row["errors"] == {"adzuna": "boom"}


def test_views_are_queryable(conn):
    for view in ("v_inbox", "v_tracking", "v_rejected_recent"):
        conn.execute(f"select * from {view}").fetchall()
