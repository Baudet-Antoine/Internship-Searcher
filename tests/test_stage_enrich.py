from datetime import UTC, datetime

from stage_radar import db
from stage_radar.engines.gemini import GeminiQuotaError
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key
from stage_radar.stages.enrich import Extraction, finalize, run_enrich

RATES = {"GBP": 0.85}


def test_finalize_uses_regex_fallback_and_validates():
    ex = Extraction(summary_fr="Résumé.", start_date="2027-01", duration_months=6,
                    key_requirements=["Python", "SQL", "a", "b", "c", "d"])
    extracted, summary = finalize(ex, "Salary: €1,400/month", RATES)
    assert extracted["salary_eur_month"] == 1400 and extracted["salary_source"] == "regex"
    assert extracted["start_date"] == "2027-01" and extracted["duration_months"] == 6
    assert len(extracted["key_requirements"]) == 5 and summary == "Résumé."


def test_finalize_drops_implausible_values():
    ex = Extraction(salary_amount=90000, salary_currency="EUR", salary_period="month",
                    start_date="2031-05", duration_months=40)
    extracted, _ = finalize(ex, "", RATES)
    assert extracted["salary_eur_month"] is None
    assert extracted["start_date"] is None and extracted["duration_months"] is None
    assert len(extracted["warnings"]) == 3


class StubClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate_json(self, system, prompt, schema):
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_run_enrich(conn):
    ids = []
    for i in range(2):
        raw = RawOffer("fake", str(i), "u", f"Data Intern {i}", f"C{i}", "London", "GB", None,
                       "desc", False, datetime(2026, 9, 28, tzinfo=UTC))
        offer_id, _ = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
        db.update_offer(conn, offer_id, status=OfferStatus.CLASSIFIED)
        ids.append(offer_id)
    conn.commit()
    client = StubClient([
        {"salary_amount": 2000, "salary_currency": "GBP", "salary_period": "month",
         "city": "London", "summary_fr": "Mission RAG.", "key_requirements": ["Python"]},
        GeminiQuotaError("quota"),
    ])
    report = RunReport()
    run_enrich(conn, client, RATES, report)
    [enriched] = db.fetch_offers(conn, [OfferStatus.ENRICHED])
    assert enriched["summary"] == "Mission RAG." and enriched["city"] == "London"
    assert enriched["extracted"]["salary_eur_month"] == round(2000 / 0.85)
    assert len(db.fetch_offers(conn, [OfferStatus.CLASSIFIED])) == 1
    assert report.counts["enrich"] == {"enriched": 1} and "quota" in report.errors["enrich"]
