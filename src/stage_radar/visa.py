from __future__ import annotations

from datetime import date, timedelta

from stage_radar.config import Country


def apply_deadline(country: Country, latest_start: date, recruitment_weeks: int) -> date:
    """Dernier jour réaliste pour postuler : démarrage max − délai visa − recrutement."""
    return latest_start - timedelta(weeks=country.visa_lead_weeks + recruitment_weeks)
