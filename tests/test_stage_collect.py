from datetime import UTC, date, datetime

from stage_radar.models import RawOffer, RunReport
from stage_radar.stages.collect import run_collect


def raw(i, title="Data Science Intern"):
    return RawOffer(source="fake", source_id=str(i), url=f"https://x/{i}", title=title,
                    company=f"Company {i}", location_raw="Berlin", country="DE", city="Berlin",
                    description="d", description_is_full=False,
                    posted_at=datetime(2026, 9, 20, tzinfo=UTC))


class FakeCollector:
    name = "fake"

    def fetch(self, since):
        yield raw(1)
        yield raw(2)
        yield raw(3, title="")  # titre vide : ignoré


class FailingCollector:
    name = "broken"

    def fetch(self, since):
        yield raw(10)
        raise RuntimeError("api down app_key=secret")


def test_run_collect_isolates_failures(conn):
    report = RunReport()
    run_collect(conn, [FailingCollector(), FakeCollector()], date(2026, 9, 1), report)
    assert conn.execute("select count(*) as n from offers").fetchone()["n"] == 3
    assert report.counts["collect"] == {"broken_seen": 1, "broken_new": 1,
                                        "fake_seen": 2, "fake_new": 2}
    assert "api down" in report.errors["broken"] and "secret" not in report.errors["broken"]
