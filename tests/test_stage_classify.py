import logging
from datetime import UTC, datetime

from stage_radar import db
from stage_radar.config import load_yaml
from stage_radar.engines.base import Decision, load_questions
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import GeminiQuotaError, GeminiRequestError
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key
from stage_radar.stages.classify import run_classify

QUESTIONS = load_questions(load_yaml("decisions.yaml"))


def add_prefiltered(conn, n):
    for i in range(n):
        raw = RawOffer("fake", str(i), "u", f"Data Intern {i}", f"C{i}", "Berlin", "DE",
                       "Berlin", "desc", False, datetime(2026, 9, 28, tzinfo=UTC))
        offer_id, _ = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
        db.update_offer(conn, offer_id, status=OfferStatus.PREFILTERED)
    conn.commit()


class ScriptedEngine:
    name = "scripted"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def classify(self, text, profile, questions):
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_run_classify(conn, caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    add_prefiltered(conn, 3)
    base = FakeEngine().classify("", "", QUESTIONS)
    engine = ScriptedEngine([
        base,
        {**base, "work_mode": Decision("remote", 0.95)},
        GeminiQuotaError("quota"),
    ])
    report = RunReport()
    run_classify(conn, engine, QUESTIONS, "profile", (0.85, 0.6), "v1", report)
    assert len(db.fetch_offers(conn, [OfferStatus.CLASSIFIED])) == 1
    [rejected] = db.fetch_offers(conn, [OfferStatus.REJECTED])
    assert rejected["rejected_stage"] == "classify"
    assert rejected["decisions"]["work_mode"] == {"answer": "remote", "p": 0.95}
    assert len(db.fetch_offers(conn, [OfferStatus.PREFILTERED])) == 1  # repris au prochain run
    assert report.counts["classify"] == {"passed": 1, "rejected_work_mode": 1}
    assert "quota" in report.errors["classify"]
    assert any(m.startswith("classify 1/3") and "retenue" in m for m in caplog.messages)
    assert any(m.startswith("classify 2/3") and "rejetée (work_mode)" in m
               for m in caplog.messages)
    assert "classify : arrêt, 1 offre(s) reprise(s) au prochain passage" in caplog.messages


def test_run_classify_stops_at_once_on_permanent_gemini_error(conn, caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    add_prefiltered(conn, 4)
    engine = ScriptedEngine([GeminiRequestError("Gemini HTTP 404 : model not found")])
    report = RunReport()
    run_classify(conn, engine, QUESTIONS, "profile", (0.85, 0.6), "v1", report)
    assert len(db.fetch_offers(conn, [OfferStatus.PREFILTERED])) == 4
    assert "404" in report.errors["classify"]
    assert "classify : arrêt, 4 offre(s) reprise(s) au prochain passage" in caplog.messages


def test_run_classify_circuit_breaker_after_consecutive_errors(conn, caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    add_prefiltered(conn, 6)
    base = FakeEngine().classify("", "", QUESTIONS)
    engine = ScriptedEngine([RuntimeError("a"), base, RuntimeError("b"), RuntimeError("c"),
                             RuntimeError("d")])
    report = RunReport()
    run_classify(conn, engine, QUESTIONS, "profile", (0.85, 0.6), "v1", report)
    assert len(db.fetch_offers(conn, [OfferStatus.CLASSIFIED])) == 1
    assert len(db.fetch_offers(conn, [OfferStatus.PREFILTERED])) == 5
    assert any("3 erreurs consécutives" in m for m in caplog.messages)
    assert any("RuntimeError: b" in m for m in caplog.messages)  # message affiché


class CombinedEngine:
    """Moteur capable de décider et d'extraire en un seul appel (comme Gemini)."""

    name = "combined"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def classify(self, text, profile, questions):
        raise AssertionError("classify_and_extract doit être préféré")

    def classify_and_extract(self, text, profile, questions):
        return self.outputs.pop(0)


def test_run_classify_uses_single_call_extraction(conn):
    from stage_radar.extraction import Extraction

    add_prefiltered(conn, 2)
    base = FakeEngine().classify("", "", QUESTIONS)
    extraction = Extraction(salary_amount=1800, salary_currency="EUR", salary_period="month",
                            city="Berlin", summary_fr="Mission data.", key_requirements=["SQL"])
    engine = CombinedEngine([
        (base, extraction),
        ({**base, "work_mode": Decision("remote", 0.95)}, extraction),
    ])
    report = RunReport()
    run_classify(conn, engine, QUESTIONS, "profile", (0.85, 0.6), "v1", report, rates={})
    [kept] = db.fetch_offers(conn, [OfferStatus.ENRICHED])
    assert kept["summary"] == "Mission data." and kept["extracted"]["salary_eur_month"] == 1800
    app = conn.execute("select user_status from applications where offer_id = %s",
                       (kept["id"],)).fetchone()
    assert app["user_status"] == "new"  # le trigger a bien vu le passage par classified
    [rejected] = db.fetch_offers(conn, [OfferStatus.REJECTED])
    assert rejected["summary"] is None
    assert db.fetch_to_enrich(conn) == []  # plus besoin de l'étape enrich
