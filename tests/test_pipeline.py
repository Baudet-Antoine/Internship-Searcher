import logging
from datetime import UTC, date, datetime

from stage_radar import db
from stage_radar.engines.fake import FakeEngine
from stage_radar.models import RawOffer
from stage_radar.pipeline import STAGES, Components, load_settings, run_pipeline

TODAY = date(2026, 10, 1)


class OneOfferCollector:
    name = "fake"

    def fetch(self, since):
        yield RawOffer("fake", "1", "https://x/1", "Data Science Intern (m/w/d)", "Zalando SE",
                       "Berlin", "DE", "Berlin", "6 Monate, €1,500/month", True,
                       datetime(2026, 9, 30, tzinfo=UTC))
        yield RawOffer("fake", "2", "https://x/2", "Marketing Intern", "Other", "Berlin", "DE",
                       "Berlin", "x", True, datetime(2026, 9, 30, tzinfo=UTC))


class BrokenCollector:
    name = "broken"

    def fetch(self, since):
        raise RuntimeError("down")
        yield


class StubLLM:
    def generate_json(self, system, prompt, schema):
        return {"summary_fr": "Mission data.", "key_requirements": ["Python"], "city": "Berlin"}


class CaptureSender:
    def __init__(self):
        self.sent = []

    def send(self, subject, html, text):
        self.sent.append(text)


def components(collectors):
    return Components(collectors=collectors, engine=FakeEngine(), llm=StubLLM(),
                      sender=CaptureSender(), rates_loader=lambda: {"GBP": 0.85})


def test_end_to_end(conn, caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    comps = components([OneOfferCollector()])
    code = run_pipeline(conn, comps, load_settings(), STAGES, TODAY)
    assert code == 0
    [text] = comps.sender.sent
    assert "Data Science Intern" in text and "Mission data." in text
    assert "Marketing Intern" in text  # audit des rejets
    run = conn.execute("select counts, finished_at from runs").fetchone()
    assert run["finished_at"] is not None
    assert run["counts"]["classify"] == {"passed": 1}
    [offer] = db.fetch_scorable(conn)
    assert offer["status"] == "notified" and offer["extracted"]["salary_eur_month"] == 1500
    messages = caplog.messages
    for expected in ("collect fake : terminé", "prefilter : terminé", "classify : terminé",
                     "enrich : terminé", "notify : digest envoyé — 1 offre(s)",
                     "run terminé en"):
        assert any(m.startswith(expected) for m in messages), expected


def test_all_sources_failing_returns_error_code(conn):
    comps = components([BrokenCollector()])
    assert run_pipeline(conn, comps, load_settings(), STAGES, TODAY) == 1
    assert "down" in comps.sender.sent[0]  # le digest part quand même avec l'erreur
