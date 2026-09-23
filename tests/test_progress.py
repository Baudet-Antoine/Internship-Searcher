import logging

from stage_radar.progress import Progress, fmt_duration


def test_fmt_duration():
    assert fmt_duration(42) == "42 s"
    assert fmt_duration(185) == "3 min 05 s"
    assert fmt_duration(3720) == "1 h 02 min"


def test_progress_logs_every_n_steps_with_eta(caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    ticks = iter([0, 20, 40, 40])
    progress = Progress("classify", total=4, every=2, clock=lambda: next(ticks))
    for i in range(4):
        progress.step(f"offre {i}")
    progress.done("3 retenues")
    assert caplog.messages == [
        "classify : début (4 offres)",
        "classify 2/4 · 20 s · reste ~20 s · offre 1",
        "classify 4/4 · 40 s · reste ~0 s · offre 3",
        "classify : terminé en 40 s — 3 retenues",
    ]


def test_progress_without_total(caplog):
    caplog.set_level(logging.INFO, logger="stage_radar")
    ticks = iter([0, 5, 9])
    progress = Progress("collect adzuna", every=50, clock=lambda: next(ticks))
    for _ in range(50):
        progress.step()
    progress.done()
    assert caplog.messages == [
        "collect adzuna : début",
        "collect adzuna 50 · 5 s",
        "collect adzuna : terminé en 9 s",
    ]
