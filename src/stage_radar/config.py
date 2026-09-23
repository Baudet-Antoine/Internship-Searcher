"""Chargement de la configuration versionnée (config/*.yaml, seeds/*.csv)."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("STAGE_RADAR_ROOT") or Path(__file__).resolve().parents[2])


def load_yaml(name: str, root: Path = ROOT) -> dict:
    with open(root / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    visa_lead_weeks: int
    cost_of_living_eur: int
    in_scope: bool


def load_countries(root: Path = ROOT) -> dict[str, Country]:
    with open(root / "seeds" / "countries.csv", encoding="utf-8", newline="") as f:
        return {
            row["code"]: Country(
                code=row["code"],
                name=row["name"],
                visa_lead_weeks=int(row["visa_lead_weeks"]),
                cost_of_living_eur=int(row["cost_of_living_eur"]),
                in_scope=row["in_scope"].strip().lower() == "true",
            )
            for row in csv.DictReader(f)
        }


def load_city_costs(root: Path = ROOT) -> dict[str, int]:
    """Coût de la vie mensuel par ville, indexé par nom de ville normalisé."""
    from stage_radar.normalize import normalize_text

    with open(root / "seeds" / "city_costs.csv", encoding="utf-8", newline="") as f:
        return {normalize_text(r["city"]): int(r["cost_of_living_eur"]) for r in csv.DictReader(f)}
