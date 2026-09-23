"""Lignes de progression des étapes (console locale et logs GitHub Actions)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger("stage_radar")


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


class Progress:
    """Compteur d'une étape : logue toutes les `every` unités, avec temps restant estimé."""

    def __init__(self, label: str, total: int | None = None, every: int = 1,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.label, self.total, self.every, self.clock = label, total, every, clock
        self.count = 0
        self.start = clock()
        suffix = f" ({total} offres)" if total is not None else ""
        log.info("%s : début%s", label, suffix)

    def step(self, detail: str = "") -> None:
        self.count += 1
        if self.count % self.every and self.count != self.total:
            return
        elapsed = self.clock() - self.start
        if self.total:
            remaining = elapsed / self.count * (self.total - self.count)
            message = (f"{self.label} {self.count}/{self.total} · {fmt_duration(elapsed)}"
                       f" · reste ~{fmt_duration(remaining)}")
        else:
            message = f"{self.label} {self.count} · {fmt_duration(elapsed)}"
        log.info(message + (f" · {detail}" if detail else ""))

    def done(self, summary: str = "") -> None:
        elapsed = fmt_duration(self.clock() - self.start)
        log.info("%s : terminé en %s%s", self.label, elapsed, f" — {summary}" if summary else "")
