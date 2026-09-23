from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OfferStatus(StrEnum):
    COLLECTED = "collected"
    PREFILTERED = "prefiltered"
    CLASSIFIED = "classified"
    ENRICHED = "enriched"
    NOTIFIED = "notified"
    REJECTED = "rejected"


@dataclass
class RawOffer:
    source: str
    source_id: str
    url: str
    title: str
    company: str | None
    location_raw: str
    country: str | None
    city: str | None
    description: str
    description_is_full: bool
    posted_at: datetime | None
    raw: dict = field(default_factory=dict)


@dataclass
class RunReport:
    """Compteurs et erreurs d'une exécution, persistés dans la table runs."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def count(self, stage: str, key: str, n: int = 1) -> None:
        bucket = self.counts.setdefault(stage, {})
        bucket[key] = bucket.get(key, 0) + n

    def error(self, where: str, message: str) -> None:
        self.errors[where] = message
