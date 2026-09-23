from datetime import UTC, date, datetime

from stage_radar.config import Country, load_countries, load_yaml
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.rules import Rules, evaluate
from stage_radar.stages.prefilter import run_prefilter
from stage_radar.visa import apply_deadline

RULES = Rules.from_config(load_yaml("rules.yaml"))
COUNTRIES = load_countries()
TODAY = date(2026, 10, 1)


def offer(title="Data Science Intern", country="DE", posted=datetime(2026, 9, 28, tzinfo=UTC),
          description=""):
    return {"title": title, "country": country, "posted_at": posted, "description": description}


def test_apply_deadline():
    us = Country("US", "États-Unis", 12, 2200, True)
    assert apply_deadline(us, date(2027, 3, 1), 4) == date(2026, 11, 9)
    de = Country("DE", "Allemagne", 0, 1100, True)
    assert apply_deadline(de, date(2027, 3, 1), 4) == date(2027, 2, 1)


def test_valid_offer_passes():
    assert evaluate(offer(), RULES, COUNTRIES, TODAY).passed


def test_german_compound_praktikum_passes():
    title = "Pflichtpraktikum Data Analytics (m/w/d)"
    assert evaluate(offer(title), RULES, COUNTRIES, TODAY).passed


def test_country_rules():
    assert evaluate(offer(country="FR"), RULES, COUNTRIES, TODAY).code == "country"
    assert evaluate(offer(country=None), RULES, COUNTRIES, TODAY).code == "country"
    assert evaluate(offer(country="BR"), RULES, COUNTRIES, TODAY).code == "country"


def test_title_rules():
    assert evaluate(offer("Marketing Intern"), RULES, COUNTRIES, TODAY).code == "title_data"
    assert evaluate(offer("Data Scientist"), RULES, COUNTRIES, TODAY).code == "title_internship"
    assert evaluate(offer("HTML Intern"), RULES, COUNTRIES, TODAY).code == "title_data"
    verdict = evaluate(offer("Summer Intern Data Science"), RULES, COUNTRIES, TODAY)
    assert verdict.code == "title_blacklist" and "summer" in verdict.reason


def test_internship_term_in_description_is_enough():
    title = "Data Science & Analytics (m/w/d) – langfristig"
    assert evaluate(offer(title), RULES, COUNTRIES, TODAY).code == "title_internship"
    ok = offer(title, description="Pflichtpraktikum für 6 Monate im Team Data")
    assert evaluate(ok, RULES, COUNTRIES, TODAY).passed


def test_too_old():
    old = offer(posted=datetime(2026, 8, 1, tzinfo=UTC))
    assert evaluate(old, RULES, COUNTRIES, TODAY).code == "too_old"
    assert evaluate(offer(posted=None), RULES, COUNTRIES, TODAY).passed


def test_visa_window():
    assert evaluate(offer(country="US"), RULES, COUNTRIES, date(2026, 11, 9)).passed
    closed = evaluate(offer(country="US"), RULES, COUNTRIES, date(2026, 11, 10))
    assert closed.code == "visa_window" and "2026-11-09" in closed.reason


def test_run_prefilter_moves_statuses(conn):
    from stage_radar import db
    from stage_radar.normalize import dedup_key

    for i, title in enumerate(["Data Science Intern", "Marketing Intern"]):
        raw = RawOffer("fake", str(i), "u", title, f"C{i}", "Berlin", "DE", "Berlin", "d",
                       False, datetime(2026, 9, 28, tzinfo=UTC))
        db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
    conn.commit()
    report = RunReport()
    run_prefilter(conn, RULES, COUNTRIES, TODAY, report)
    assert len(db.fetch_offers(conn, [OfferStatus.PREFILTERED])) == 1
    [rejected] = db.fetch_offers(conn, [OfferStatus.REJECTED])
    assert rejected["rejected_stage"] == "prefilter"
    assert report.counts["prefilter"] == {"passed": 1, "title_data": 1}
