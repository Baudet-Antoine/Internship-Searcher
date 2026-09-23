from datetime import UTC, date, datetime

from stage_radar import db
from stage_radar.config import load_city_costs, load_countries, load_yaml
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key
from stage_radar.rules import Rules
from stage_radar.stages.notify import NotifyContext, run_notify

CTX = NotifyContext(countries=load_countries(), city_costs=load_city_costs(),
                    rules=Rules.from_config(load_yaml("rules.yaml")),
                    scoring_cfg=load_yaml("scoring.yaml"))
TODAY = date(2026, 10, 1)


class CaptureSender:
    def __init__(self):
        self.sent = []

    def send(self, subject, html, text):
        self.sent.append((subject, html, text))


def add(conn, i, status, fit="4", country="NL", city="Amsterdam"):
    raw = RawOffer("adzuna", str(i), f"https://x/{i}", f"Data Intern {i}", f"C{i}", city,
                   country, city, "desc", False, datetime(2026, 9, 30, tzinfo=UTC))
    offer_id, _ = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
    db.update_offer(conn, offer_id, status=status,
                    decisions={"profile_fit": {"answer": fit, "p": 0.9}},
                    extracted={"salary_eur_month": 2000}, summary=f"Résumé {i}")
    conn.commit()
    return offer_id


def test_run_notify_scores_sends_and_marks(conn):
    started = conn.execute("select now() - interval '1 minute' as t").fetchone()["t"]
    low = add(conn, 1, OfferStatus.ENRICHED, fit="2")
    high = add(conn, 2, OfferStatus.CLASSIFIED, fit="5")
    rejected = add(conn, 3, OfferStatus.PREFILTERED)
    db.reject(conn, rejected, "classify", "mode de travail = remote (p=0.95)")
    conn.commit()
    sender, report = CaptureSender(), RunReport()
    run_notify(conn, sender, CTX, TODAY, started, report)

    [(subject, html, text)] = sender.sent
    assert "2 nouvelle(s) offre(s)" in subject
    assert text.index("Data Intern 2") < text.index("Data Intern 1")  # tri par score
    assert "🟢 +300 €/mois" in text  # 2000 - 1700 (Amsterdam)
    assert "mode de travail = remote" in text  # audit
    assert db.fetch_unnotified(conn) == []
    scored = {o["id"]: o for o in db.fetch_scorable(conn)}
    assert scored[high]["score"] > scored[low]["score"]
    assert scored[high]["score_breakdown"]["fit"] == 1.0
    assert report.counts["notify"] == {"sent": 2}


def test_run_notify_sends_on_empty_day(conn):
    sender = CaptureSender()
    run_notify(conn, sender, CTX, TODAY, datetime(2026, 10, 1, tzinfo=UTC), RunReport())
    assert "0 nouvelle(s) offre(s)" in sender.sent[0][0]
