# Stage Radar v1.0 — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** pipeline quotidien (GitHub Actions) qui collecte des offres de stage Data Science hors de France
(Adzuna et Bundesagentur für Arbeit), les filtre par règles puis avec Gemini, les enrichit, les classe et
envoie un digest email, avec un état persistant dans Supabase Postgres.

**Architecture :** la base de données sert de machine à états (`collected → prefiltered → classified →
enriched → notified | rejected`). Chaque étape est un module indépendant qui lit un statut et en écrit un
autre. La logique métier est écrite en fonctions pures, testées unitairement. Les étapes sont de minces
couches SQL, testées contre un vrai Postgres (`pgserver` en local et en CI, ou `TEST_DATABASE_URL`).

**Tech Stack :** Python 3.12, psycopg 3, httpx, tenacity, pydantic 2, PyYAML, Jinja2, python-dotenv ;
pytest, respx, ruff, pgserver ; API REST de Gemini et de Resend ; GitHub Actions.

Spec : `docs/superpowers/specs/2026-09-23-stage-radar-design.md`.

## Global Constraints

- Coût 0 € : uniquement des paliers gratuits.
- Python ≥ 3.12. Package `stage_radar` dans `src/`. CLI : `python -m stage_radar <commande>`.
- Aucune donnée d'offre ni aucun secret dans le dépôt. Les secrets passent par variables d'environnement (`.env` en local, GitHub Secrets en CI).
- Accès base : `DATABASE_URL` (Postgres). En production, utiliser l'URL du **pooler** Supabase (compatible IPv4, requis sur GitHub Actions).
- Dernier démarrage accepté : `2027-03-01`. Durée ≥ 6 mois. Hors France. Pas de full remote. Langues : EN/FR/ES.
- Seuils de décision : rejet si p ≥ 0.85 ; flag si réponse éliminatoire avec p < 0.85, si « unspecified », ou si p < 0.6 sur une question éliminatoire.
- Poids du score : fit 0.45, fraîcheur 0.20, urgence 0.15, finances 0.10, certitude 0.10.
- Plausibilité du salaire : 300 à 6000 €/mois.
- Les messages d'erreur sont passés par `redact()` avant d'être stockés ou envoyés (pas de clé d'API dans les logs).

## Écarts assumés par rapport à la spec (reportés dans la spec à la fin)

- Les référentiels `countries` et `city_costs` sont des **CSV versionnés** lus par le code, et non des tables (aucune requête SQL n'en a besoin en v1).
- Les secrets Supabase sont remplacés par un unique `DATABASE_URL`.
- Colonne supplémentaire `offers.flags jsonb`.
- La liste noire de titres est réduite (`senior`, `head of`, `director`, `summer`, `part time`, `teilzeit`) : la liste blanche « stage » élimine déjà les postes non-stage, et « manager/lead » tuaient des stages valides.
- `eval_labels` et l'outillage d'évaluation sont reportés en v1.1 avec Laya.

## Structure des fichiers

```
pyproject.toml, .gitignore, .env.example, README.md
config/  search.yaml rules.yaml decisions.yaml scoring.yaml profile.yaml
seeds/   countries.csv city_costs.csv
supabase/migrations/20260923000000_init.sql
src/stage_radar/
  __init__.py  __main__.py (CLI)  config.py  models.py  normalize.py  db.py
  visa.py  rules.py  salary.py  scoring.py  digest.py  emailer.py  pipeline.py
  collectors/  __init__.py base.py adzuna.py arbeitsagentur.py
  engines/     __init__.py base.py fake.py gemini.py
  stages/      __init__.py collect.py prefilter.py classify.py enrich.py notify.py
  templates/   digest.html.j2 digest.txt.j2
tests/ conftest.py fixtures/*.json test_*.py
.github/workflows/ ci.yml daily.yml
```

---

### Task 1 : scaffold, configuration, modèles, normalisation

**Files :**
- Create : `pyproject.toml`, `.gitignore`, `.env.example`, `config/*.yaml`, `seeds/*.csv`, `src/stage_radar/{__init__,config,models,normalize}.py`
- Test : `tests/test_config.py`, `tests/test_normalize.py`

**Interfaces :**
- Produces : `config.ROOT`, `config.load_yaml(name) -> dict`, `config.Country(code,name,visa_lead_weeks,cost_of_living_eur,in_scope)`, `config.load_countries() -> dict[str, Country]`, `config.load_city_costs() -> dict[str, int]` (clé = ville normalisée) ; `models.OfferStatus` (StrEnum), `models.RawOffer`, `models.RunReport(counts, errors).count(stage,key,n=1)/.error(where,msg)` ; `normalize.strip_html`, `normalize_text`, `normalize_title`, `normalize_company`, `dedup_key(company,title,country) -> str`, `parse_dt(value) -> datetime|None` (toujours tz-aware).

- [ ] **Step 1 : créer les fichiers de projet et de configuration**

`pyproject.toml` :
```toml
[project]
name = "stage-radar"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "psycopg[binary]>=3.2",
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "jinja2>=3.1",
  "tenacity>=8.3",
  "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "respx>=0.21", "ruff>=0.5", "pgserver>=0.1.4"]

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
stage_radar = ["templates/*.j2"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

`.gitignore` :
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.ruff_cache/
.env
out/
```

`.env.example` :
```
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-0-eu-west-3.pooler.supabase.com:5432/postgres
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
GEMINI_API_KEY=
RESEND_API_KEY=
DIGEST_TO=you@example.com
```

`config/search.yaml` :
```yaml
lookback_days_first_run: 30
overlap_days: 2
adzuna:
  enabled: true
  countries: [gb, de, nl, be, at, ch, es, it, pl, ca, us, sg, au]
  queries: ["data science intern", "machine learning intern", "data internship", "AI intern"]
  extra_queries:
    de: ["praktikum data"]
    at: ["praktikum data"]
    ch: ["praktikum data"]
    nl: ["stage data"]
    be: ["stage data"]
    es: ["prácticas data"]
    it: ["stage data"]
  max_pages: 2
  results_per_page: 50
arbeitsagentur:
  enabled: true
  queries: ["Data Science", "Machine Learning", "Data Analytics", "Künstliche Intelligenz"]
  max_pages: 2
  page_size: 50
```

`config/rules.yaml` :
```yaml
latest_start: 2027-03-01
recruitment_weeks: 4
max_age_days: 45
excluded_countries: [FR]
# Regex appliquées au titre normalisé (minuscules, sans accents, ponctuation -> espace)
title_data_patterns:
  - '\bdata\b'
  - '\bdaten\w*'
  - '\bdatos\b'
  - '\bdati\b'
  - '\bdonnees\b'
  - '\bmachine learning\b'
  - '\bdeep learning\b'
  - '\bml\b'
  - '\bai\b'
  - '\bki\b'
  - '\bia\b'
  - '\bgenai\b'
  - '\bllms?\b'
  - '\bnlp\b'
  - '\bcomputer vision\b'
  - '\banalytics?\b'
  - '\banalyst\b'
  - '\bscien(ce|tist)s?\b'
  - '\bquant\w*'
  - '\bbusiness intelligence\b'
  - '\bbi\b'
  - '\bartificial intelligence\b'
  - '\bkunstliche intelligenz\b'
  - '\binteligencia artificial\b'
title_internship_patterns:
  - '\binterns?\b'
  - '\binternships?\b'
  - 'stage\b'
  - '\bstagiaire\b'
  - '\bstagiair\w*'
  - 'praktik\w*'
  - '\bpracticas\b'
  - '\bpracticante\b'
  - '\bbecari[oa]s?\b'
  - '\bstagista\b'
  - '\btirocini\w*'
  - '\bstaz\w*'
  - '\bpraktyk\w*'
title_blacklist_patterns:
  - '\bsenior\b'
  - '\bhead of\b'
  - '\bdirector\b'
  - '\bsummer\b'
  - '\bpart time\b'
  - '\bteilzeit\b'
```

`config/decisions.yaml` :
```yaml
engine: gemini
gemini_model: gemini-2.5-flash
gemini_min_interval_s: 4.5
reject_threshold: 0.85
uncertain_below: 0.6
questions:
  - id: is_internship_convention
    label: stage sous convention
    kind: bool
    prompt: "Is this an internship a student can do under a university internship agreement (not a permanent or fixed-term job, not a working-student or part-time job, not a graduate programme)?"
    reject_answers: ["no"]
  - id: local_enrollment_required
    label: inscription locale exigée
    kind: bool
    prompt: "Does the offer require being enrolled at a university located in the job's country (e.g. 'must be enrolled at a German university')?"
    reject_answers: ["yes"]
  - id: duration
    label: durée
    kind: choice
    prompt: "What is the internship duration? lt4 = under 4 months, 4to5 = 4 to 5 months, 6plus = 6 months or more, unspecified = not stated."
    choices: ["lt4", "4to5", "6plus", "unspecified"]
    reject_answers: ["lt4", "4to5"]
    flag_answers: ["unspecified"]
    flag_text: "durée non précisée"
  - id: start
    label: démarrage
    kind: choice
    prompt: "When does the internship start? before_dec = before December 2026; dec_mar = December 2026 to March 2027 inclusive; after_mar = after March 2027; unspecified = not stated, 'as soon as possible' or 'flexible'."
    choices: ["before_dec", "dec_mar", "after_mar", "unspecified"]
    reject_answers: ["before_dec", "after_mar"]
    flag_answers: ["unspecified"]
    flag_text: "date de démarrage non précisée"
  - id: work_mode
    label: mode de travail
    kind: choice
    prompt: "What is the work mode? onsite, hybrid, remote (fully remote), unspecified."
    choices: ["onsite", "hybrid", "remote", "unspecified"]
    reject_answers: ["remote"]
    flag_answers: ["unspecified"]
    flag_text: "mode de travail non précisé"
  - id: other_language_required
    label: langue exigée
    kind: bool
    prompt: "Does the offer REQUIRE fluency in a language other than English, French or Spanish (e.g. German, Dutch, Swedish)? Answer 'no' if such a language is only a plus or if the offer is written in it without stating a requirement."
    reject_answers: ["yes"]
  - id: profile_fit
    label: adéquation profil
    kind: score
    include_profile: true
    prompt: "How well does the candidate profile fit this internship? 1 = unrelated, 5 = excellent fit."
```

`config/scoring.yaml` :
```yaml
top_n: 15
weights:
  fit: 0.45
  freshness: 0.20
  urgency: 0.15
  finance: 0.10
  certainty: 0.10
freshness_days: 14
urgency_full_days: 21
urgency_zero_days: 90
closing_window_days: 30
```

`config/profile.yaml` :
```yaml
education: "BSc Data Science for Responsible Business — emlyon Business School & École Centrale de Lyon (diplôme sept. 2027), GPA 4.0/4.0; échange Sungkyunkwan University (Computer Science)"
languages: ["French (native)", "English (bilingual, C1 certified)", "Spanish (B1)"]
experience:
  - "Data Science & Development intern (12 months): built an AI-driven market research platform (LLM, national open data) for agri-food SMEs"
  - "Research assistant intern, University of Brescia: data management with blockchain (Hyperledger Fabric), LLMs and knowledge graphs"
  - "Unity game developer intern: C#, databases, LiveOps"
projects:
  - "Full-stack trading platform (React, JavaScript, Chromium extension, TLSNotary, self-hosted VPS)"
  - "Reimplementation of the NeRF paper from scratch"
  - "Lap-time prediction ML model (98.5% accuracy)"
skills: ["Python", "Machine Learning", "Deep Learning", "LLM / RAG", "Knowledge graphs", "Statistics", "Big Data", "SQL", "React / JavaScript", "AWS (Cloud Practitioner)"]
interests: "Applied ML, LLM products, data-driven products with business impact"
```

`seeds/countries.csv` (délais visa et coût de la vie étudiant mensuel : **estimations grossières**, à ajuster) :
```csv
code,name,visa_lead_weeks,cost_of_living_eur,in_scope
FR,France,0,1100,false
DE,Allemagne,0,1100,true
NL,Pays-Bas,0,1400,true
BE,Belgique,0,1150,true
AT,Autriche,0,1100,true
CH,Suisse,2,2200,true
ES,Espagne,0,1000,true
IT,Italie,0,1000,true
PL,Pologne,0,750,true
IE,Irlande,0,1600,true
PT,Portugal,0,900,true
SE,Suède,0,1200,true
DK,Danemark,0,1400,true
FI,Finlande,0,1100,true
NO,Norvège,0,1500,true
LU,Luxembourg,0,1500,true
CZ,Tchéquie,0,850,true
GB,Royaume-Uni,6,1500,true
US,États-Unis,12,2200,true
CA,Canada,16,1600,true
SG,Singapour,6,1800,true
AU,Australie,8,1700,true
```

`seeds/city_costs.csv` :
```csv
city,country,cost_of_living_eur
London,GB,2000
Zurich,CH,2500
Zürich,CH,2500
Geneva,CH,2500
Amsterdam,NL,1700
Munich,DE,1400
München,DE,1400
Berlin,DE,1200
Dublin,IE,1900
Copenhagen,DK,1600
Stockholm,SE,1400
Oslo,NO,1700
Barcelona,ES,1200
Madrid,ES,1150
Milan,IT,1200
New York,US,3000
San Francisco,US,3200
Singapore,SG,1800
```

- [ ] **Step 2 : écrire les tests qui échouent**

`tests/test_config.py` :
```python
from stage_radar.config import load_city_costs, load_countries, load_yaml


def test_countries_seed():
    countries = load_countries()
    assert countries["DE"].visa_lead_weeks == 0
    assert countries["US"].visa_lead_weeks == 12
    assert countries["US"].in_scope is True
    assert countries["FR"].in_scope is False


def test_city_costs_are_keyed_by_normalized_name():
    costs = load_city_costs()
    assert costs["london"] == 2000
    assert costs["munchen"] == 1400


def test_yaml_configs_load():
    assert load_yaml("rules.yaml")["max_age_days"] == 45
    questions = load_yaml("decisions.yaml")["questions"]
    convention = next(q for q in questions if q["id"] == "is_internship_convention")
    assert convention["reject_answers"] == ["no"]  # pas de booléen YAML
    assert abs(sum(load_yaml("scoring.yaml")["weights"].values()) - 1.0) < 1e-9
```

`tests/test_normalize.py` :
```python
from stage_radar.normalize import (
    dedup_key,
    normalize_company,
    normalize_title,
    parse_dt,
    strip_html,
)


def test_normalize_title_removes_gender_tags_and_accents():
    assert normalize_title("Data Scientist Intern (m/w/d)") == "data scientist intern"
    assert normalize_title("Praktikum KI (f/m/x)") == "praktikum ki"
    assert normalize_title("Stage Données (all genders)") == "stage donnees"


def test_normalize_company_strips_legal_suffixes():
    assert normalize_company("Adyen N.V.") == "adyen"
    assert normalize_company("Zalando SE") == "zalando"
    assert normalize_company("Siemens AG") == "siemens"
    assert normalize_company(None) == ""


def test_dedup_key_is_stable_across_sources():
    a = dedup_key("Adyen N.V.", "Data Science Intern (m/f/d)", "NL")
    b = dedup_key("adyen", "Data Science Intern", "nl")
    assert a == b
    assert a != dedup_key("adyen", "Data Science Intern", "DE")


def test_strip_html():
    assert strip_html("<strong>Data</strong> &amp; AI") == "Data & AI"


def test_parse_dt():
    assert parse_dt("2026-09-20T10:00:00Z").tzinfo is not None
    assert parse_dt("2026-09-20").day == 20
    assert parse_dt("2026-09-20").tzinfo is not None
    assert parse_dt("pas une date") is None
    assert parse_dt(None) is None
```

- [ ] **Step 3 : lancer et constater l'échec**

Run : `.venv\Scripts\python -m pip install -e ".[dev]"` puis `.venv\Scripts\python -m pytest tests/test_config.py tests/test_normalize.py -q`
Expected : FAIL (`ModuleNotFoundError: stage_radar`)

- [ ] **Step 4 : implémenter**

`src/stage_radar/__init__.py` :
```python
__version__ = "0.1.0"
```

`src/stage_radar/config.py` :
```python
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
```

`src/stage_radar/models.py` :
```python
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
```

`src/stage_radar/normalize.py` :
```python
"""Normalisation de texte et clé de dédoublonnage."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime

_LEGAL_SUFFIXES = {
    "gmbh", "ag", "se", "ltd", "limited", "llc", "inc", "bv", "nv",
    "sa", "sl", "srl", "spa", "plc", "kg", "oy", "ab", "aps",
}
_GENDER_TAGS = re.compile(
    r"\(\s*(?:[mwfdxha]\s*/\s*)+[mwfdxha]\s*\)|\(\s*all genders?\s*\)|\(\s*gn\s*\)",
    re.IGNORECASE,
)
_HTML_TAGS = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str:
    text = html.unescape(_HTML_TAGS.sub(" ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_title(title: str | None) -> str:
    return normalize_text(_GENDER_TAGS.sub(" ", title or ""))


def normalize_company(company: str | None) -> str:
    text = normalize_text((company or "").replace(".", ""))
    return " ".join(t for t in text.split() if t not in _LEGAL_SUFFIXES)


def dedup_key(company: str | None, title: str | None, country: str | None) -> str:
    base = "|".join([normalize_company(company), normalize_title(title), (country or "").upper()])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
```

- [ ] **Step 5 : lancer les tests**

Run : `.venv\Scripts\python -m pytest tests/test_config.py tests/test_normalize.py -q`
Expected : PASS

- [ ] **Step 6 : commit**

```bash
git add -A
git commit -m "feat: project scaffold, configuration and normalization"
```

---

### Task 2 : schéma Postgres et couche d'accès (db.py)

**Files :**
- Create : `supabase/migrations/20260923000000_init.sql`, `src/stage_radar/db.py`, `tests/conftest.py`
- Test : `tests/test_db.py`

**Interfaces :**
- Consumes : `RawOffer`, `OfferStatus`, `RunReport`, `config.ROOT`.
- Produces : `db.connect(url) -> psycopg.Connection` (dict_row) ; `apply_migrations(conn)` ; `upsert_offer(conn, raw, dedup) -> tuple[str, bool]` (id, offre nouvelle) ; `fetch_offers(conn, statuses) -> list[dict]` ; `fetch_to_enrich(conn)` ; `fetch_unnotified(conn)` ; `fetch_scorable(conn)` ; `update_offer(conn, offer_id, **fields)` ; `reject(conn, offer_id, stage, reason, **fields)` ; `mark_notified(conn, ids)` ; `fetch_sources(conn, ids) -> dict[str, list[dict]]` ; `rejected_sample(conn, since, n) -> list[dict]` ; `start_run(conn) -> tuple[str, datetime]` ; `finish_run(conn, run_id, report)` ; `last_run_start(conn) -> datetime | None`. Dicts d'offre : clés `id` (str), `title, company, country, city, description, description_is_full, posted_at, status, decisions, extracted, flags, score, score_breakdown, summary, notified_at, rejected_stage, rejected_reason`. Fixture pytest `conn`.

- [ ] **Step 1 : écrire la migration**

`supabase/migrations/20260923000000_init.sql` :
```sql
create type offer_status as enum
  ('collected', 'prefiltered', 'classified', 'enriched', 'notified', 'rejected');
create type user_status as enum
  ('new', 'interested', 'applied', 'interview', 'offer', 'rejected', 'dismissed');

create table offers (
  id uuid primary key default gen_random_uuid(),
  dedup_key text not null unique,
  title text not null,
  company text,
  country text,
  city text,
  description text not null default '',
  description_is_full boolean not null default false,
  posted_at timestamptz,
  collected_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  status offer_status not null default 'collected',
  rejected_stage text,
  rejected_reason text,
  decisions jsonb not null default '{}',
  extracted jsonb not null default '{}',
  flags jsonb not null default '[]',
  score numeric,
  score_breakdown jsonb not null default '{}',
  summary text,
  engine_version text,
  notified_at timestamptz,
  updated_at timestamptz not null default now()
);
create index offers_status_idx on offers (status);

create table offer_sources (
  offer_id uuid not null references offers (id) on delete cascade,
  source text not null,
  source_id text not null,
  url text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  raw jsonb not null default '{}',
  primary key (source, source_id)
);
create index offer_sources_offer_idx on offer_sources (offer_id);

create table applications (
  offer_id uuid primary key references offers (id) on delete cascade,
  user_status user_status not null default 'new',
  notes text,
  label boolean,
  updated_at timestamptz not null default now()
);

create table runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  counts jsonb not null default '{}',
  errors jsonb not null default '{}'
);

create function create_application() returns trigger
language plpgsql as $$
begin
  insert into applications (offer_id) values (new.id) on conflict do nothing;
  return new;
end $$;

create trigger offers_create_application
  after update of status on offers
  for each row
  when (new.status = 'classified' and old.status is distinct from 'classified')
  execute function create_application();

create function touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger applications_touch before update on applications
  for each row execute function touch_updated_at();

alter table offers enable row level security;
alter table offer_sources enable row level security;
alter table applications enable row level security;
alter table runs enable row level security;

create view v_inbox with (security_invoker = true) as
select a.user_status, o.score, o.title, o.company, o.country, o.city, o.summary, o.flags,
       (o.extracted ->> 'salary_eur_month')::int as salary_eur_month, o.posted_at,
       (select string_agg(s.url, ' ') from offer_sources s where s.offer_id = o.id) as urls,
       o.id
from offers o
join applications a on a.offer_id = o.id
where a.user_status in ('new', 'interested') and o.status <> 'rejected'
order by o.score desc nulls last;

create view v_tracking with (security_invoker = true) as
select a.user_status, a.notes, a.updated_at, o.title, o.company, o.country, o.city,
       (select string_agg(s.url, ' ') from offer_sources s where s.offer_id = o.id) as urls,
       o.id
from offers o
join applications a on a.offer_id = o.id
where a.user_status in ('applied', 'interview', 'offer')
order by a.updated_at desc;

create view v_rejected_recent with (security_invoker = true) as
select o.updated_at, o.rejected_stage, o.rejected_reason, o.title, o.company, o.country, o.id
from offers o
where o.status = 'rejected' and o.updated_at > now() - interval '7 days'
order by o.updated_at desc;
```

- [ ] **Step 2 : fixture de test et tests qui échouent**

`tests/conftest.py` :
```python
import os

import pytest

from stage_radar import db


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield server.get_uri()


@pytest.fixture
def conn(database_url):
    c = db.connect(database_url)
    c.execute("drop schema public cascade")
    c.execute("create schema public")
    c.commit()
    db.apply_migrations(c)
    yield c
    c.close()
```

`tests/test_db.py` :
```python
from datetime import UTC, datetime

from stage_radar import db
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key


def make_raw(source="adzuna", source_id="1", company="Adyen", title="Data Science Intern",
             country="NL", description="snippet", full=False):
    return RawOffer(
        source=source, source_id=source_id, url=f"https://example.com/{source}/{source_id}",
        title=title, company=company, location_raw="Amsterdam", country=country,
        city="Amsterdam", description=description, description_is_full=full,
        posted_at=datetime(2026, 9, 20, tzinfo=UTC), raw={"id": source_id},
    )


def insert(conn, raw):
    result = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
    conn.commit()
    return result


def test_upsert_is_idempotent_per_source(conn):
    offer_id, is_new = insert(conn, make_raw())
    again_id, again_new = insert(conn, make_raw())
    assert is_new is True and again_new is False and again_id == offer_id
    assert conn.execute("select count(*) as n from offers").fetchone()["n"] == 1


def test_cross_source_duplicates_share_one_offer(conn):
    a, _ = insert(conn, make_raw(source="adzuna", source_id="1", company="Adyen N.V."))
    b, is_new = insert(conn, make_raw(source="greenhouse", source_id="9", company="adyen"))
    assert a == b and is_new is False
    assert db.fetch_sources(conn, [a])[a] == [
        {"source": "adzuna", "url": "https://example.com/adzuna/1"},
        {"source": "greenhouse", "url": "https://example.com/greenhouse/9"},
    ]


def test_full_description_replaces_snippet(conn):
    offer_id, _ = insert(conn, make_raw(description="short"))
    insert(conn, make_raw(source="ats", source_id="2", description="long text", full=True))
    [offer] = db.fetch_offers(conn, [OfferStatus.COLLECTED])
    assert offer["description"] == "long text" and offer["description_is_full"] is True


def test_classified_status_creates_application(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.CLASSIFIED, flags=["durée non précisée"],
                    decisions={"duration": {"answer": "unspecified", "p": 0.9}})
    conn.commit()
    app = conn.execute("select user_status from applications where offer_id = %s",
                       (offer_id,)).fetchone()
    assert app["user_status"] == "new"
    [offer] = db.fetch_offers(conn, [OfferStatus.CLASSIFIED])
    assert offer["flags"] == ["durée non précisée"]
    assert offer["decisions"]["duration"]["p"] == 0.9


def test_reject_and_sample(conn):
    offer_id, _ = insert(conn, make_raw())
    started = conn.execute("select now() - interval '1 minute' as t").fetchone()["t"]
    db.reject(conn, offer_id, "prefilter", "titre sans terme stage")
    conn.commit()
    [row] = db.rejected_sample(conn, started, 5)
    assert row["rejected_reason"] == "titre sans terme stage"
    assert row["title"] == "Data Science Intern"


def test_unnotified_and_mark_notified(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.ENRICHED)
    conn.commit()
    assert [o["id"] for o in db.fetch_unnotified(conn)] == [offer_id]
    db.mark_notified(conn, [offer_id])
    conn.commit()
    assert db.fetch_unnotified(conn) == []
    assert [o["id"] for o in db.fetch_scorable(conn)] == [offer_id]


def test_fetch_to_enrich_includes_notified_without_summary(conn):
    offer_id, _ = insert(conn, make_raw())
    db.update_offer(conn, offer_id, status=OfferStatus.CLASSIFIED)
    db.mark_notified(conn, [offer_id])
    conn.commit()
    assert [o["id"] for o in db.fetch_to_enrich(conn)] == [offer_id]


def test_runs_journal(conn):
    assert db.last_run_start(conn) is None
    run_id, started = db.start_run(conn)
    report = RunReport()
    report.count("collect", "adzuna_seen", 3)
    report.error("adzuna", "boom")
    db.finish_run(conn, run_id, report)
    conn.commit()
    assert db.last_run_start(conn) == started
    row = conn.execute("select counts, errors from runs").fetchone()
    assert row["counts"] == {"collect": {"adzuna_seen": 3}}
    assert row["errors"] == {"adzuna": "boom"}


def test_views_are_queryable(conn):
    for view in ("v_inbox", "v_tracking", "v_rejected_recent"):
        conn.execute(f"select * from {view}").fetchall()
```

- [ ] **Step 3 : lancer et constater l'échec**

Run : `.venv\Scripts\python -m pytest tests/test_db.py -q`
Expected : FAIL (`AttributeError: module 'stage_radar.db'` / import error)

- [ ] **Step 4 : implémenter `src/stage_radar/db.py`**

```python
"""Accès Postgres : migrations et requêtes de la machine à états."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from stage_radar.config import ROOT
from stage_radar.models import OfferStatus, RawOffer, RunReport

MIGRATIONS_DIR = ROOT / "supabase" / "migrations"

OFFER_COLUMNS = """
  id::text as id, title, company, country, city, description, description_is_full,
  posted_at, status::text as status, decisions, extracted, flags, score, score_breakdown,
  summary, notified_at, rejected_stage, rejected_reason
"""


def connect(url: str) -> psycopg.Connection:
    # prepare_threshold=None : compatible avec le pooler Supabase (pgbouncer)
    return psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    conn.execute(
        "create table if not exists schema_migrations "
        "(name text primary key, applied_at timestamptz not null default now())"
    )
    done = {r["name"] for r in conn.execute("select name from schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    conn.commit()
    return applied


def upsert_offer(conn: psycopg.Connection, raw: RawOffer, dedup: str) -> tuple[str, bool]:
    row = conn.execute(
        """
        insert into offers (dedup_key, title, company, country, city, description,
                            description_is_full, posted_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (dedup_key) do update set
          last_seen_at = now(),
          description = case when excluded.description_is_full and not offers.description_is_full
                             then excluded.description else offers.description end,
          description_is_full = offers.description_is_full or excluded.description_is_full
        returning id::text as id, (xmax = 0) as inserted
        """,
        (dedup, raw.title, raw.company, raw.country, raw.city, raw.description,
         raw.description_is_full, raw.posted_at),
    ).fetchone()
    conn.execute(
        """
        insert into offer_sources (offer_id, source, source_id, url, raw)
        values (%s, %s, %s, %s, %s)
        on conflict (source, source_id) do update set last_seen_at = now()
        """,
        (row["id"], raw.source, raw.source_id, raw.url, Jsonb(raw.raw)),
    )
    return row["id"], bool(row["inserted"])


def fetch_offers(conn: psycopg.Connection, statuses: list[OfferStatus]) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers where status = any(%s::offer_status[]) "
        "order by collected_at, id",
        ([str(s) for s in statuses],),
    ).fetchall()


def fetch_to_enrich(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status = 'classified' or (status = 'notified' and summary is null) "
        "order by collected_at, id"
    ).fetchall()


def fetch_unnotified(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status in ('classified', 'enriched') and notified_at is null "
        "order by collected_at, id"
    ).fetchall()


def fetch_scorable(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        f"select {OFFER_COLUMNS} from offers "
        "where status in ('classified', 'enriched', 'notified') order by collected_at, id"
    ).fetchall()


def update_offer(conn: psycopg.Connection, offer_id: str, **fields: Any) -> None:
    assignments, values = [], []
    for key, value in fields.items():
        cast = "::offer_status" if key == "status" else ""
        assignments.append(f"{key} = %s{cast}")
        values.append(Jsonb(value) if isinstance(value, dict | list) else value)
    assignments.append("updated_at = now()")
    conn.execute(
        f"update offers set {', '.join(assignments)} where id = %s", (*values, offer_id)
    )


def reject(conn: psycopg.Connection, offer_id: str, stage: str, reason: str, **fields: Any) -> None:
    update_offer(conn, offer_id, status=OfferStatus.REJECTED, rejected_stage=stage,
                 rejected_reason=reason, **fields)


def mark_notified(conn: psycopg.Connection, ids: list[str]) -> None:
    if not ids:
        return
    conn.execute(
        "update offers set notified_at = now(), updated_at = now(), "
        "status = case when status in ('classified', 'enriched') then 'notified'::offer_status "
        "else status end where id = any(%s::uuid[])",
        (ids,),
    )


def fetch_sources(conn: psycopg.Connection, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = conn.execute(
        "select offer_id::text as offer_id, source, url from offer_sources "
        "where offer_id = any(%s::uuid[]) order by first_seen_at, source",
        (ids,),
    ).fetchall()
    for r in rows:
        out[r["offer_id"]].append({"source": r["source"], "url": r["url"]})
    return out


def rejected_sample(conn: psycopg.Connection, since: datetime, n: int) -> list[dict]:
    return conn.execute(
        "select title, company, country, rejected_stage, rejected_reason from offers "
        "where status = 'rejected' and updated_at >= %s order by random() limit %s",
        (since, n),
    ).fetchall()


def start_run(conn: psycopg.Connection) -> tuple[str, datetime]:
    row = conn.execute(
        "insert into runs default values returning id::text as id, started_at"
    ).fetchone()
    conn.commit()
    return row["id"], row["started_at"]


def finish_run(conn: psycopg.Connection, run_id: str, report: RunReport) -> None:
    conn.execute(
        "update runs set finished_at = now(), counts = %s, errors = %s where id = %s",
        (Jsonb(report.counts), Jsonb(report.errors), run_id),
    )


def last_run_start(conn: psycopg.Connection) -> datetime | None:
    row = conn.execute(
        "select started_at from runs where finished_at is not null "
        "order by started_at desc limit 1"
    ).fetchone()
    return row["started_at"] if row else None
```

- [ ] **Step 5 : lancer les tests**

Run : `.venv\Scripts\python -m pytest tests/test_db.py -q`
Expected : PASS

- [ ] **Step 6 : commit** — `git add -A && git commit -m "feat: postgres schema and data access layer"`

---

### Task 3 : collecteurs (HTTP commun, Adzuna, Arbeitsagentur) et étape collect

**Files :**
- Create : `src/stage_radar/collectors/{__init__,base,adzuna,arbeitsagentur}.py`, `src/stage_radar/stages/{__init__,collect}.py`, `tests/fixtures/{adzuna_search,ba_search,ba_detail}.json`
- Test : `tests/test_collectors.py`, `tests/test_stage_collect.py`

**Interfaces :**
- Consumes : `RawOffer`, `RunReport`, `normalize.strip_html/parse_dt/dedup_key`, `db.upsert_offer`.
- Produces : `collectors.base.Collector` (Protocol : `name: str`, `fetch(since: date) -> Iterator[RawOffer]`) ; `SourceAuthError` ; `get_json(client, url, **kwargs)` (avec nouvelles tentatives) ; `redact(text) -> str` ; `AdzunaCollector(app_id, app_key, countries, queries, extra_queries, max_pages, results_per_page, client=None)` ; `ArbeitsagenturCollector(queries, max_pages, page_size, client=None)` ; `collectors.build_collectors(search_cfg, env) -> list[Collector]` ; `stages.collect.run_collect(conn, collectors, since, report)`.

- [ ] **Step 1 : fixtures**

`tests/fixtures/adzuna_search.json` :
```json
{
  "count": 2,
  "results": [
    {
      "id": "4811",
      "title": "<strong>Data Science</strong> Intern (m/f/d)",
      "description": "Join our fraud team for a 6-month internship...",
      "redirect_url": "https://www.adzuna.nl/details/4811",
      "created": "2026-09-21T08:00:00Z",
      "company": {"display_name": "Adyen"},
      "location": {"display_name": "Amsterdam, Noord-Holland", "area": ["Nederland", "Noord-Holland", "Amsterdam"]},
      "salary_min": 1800, "salary_max": 1800, "salary_is_predicted": "0"
    },
    {
      "id": "4812",
      "title": "Machine Learning Intern",
      "description": "Remote first...",
      "redirect_url": "https://www.adzuna.nl/details/4812",
      "created": "2026-09-22T08:00:00Z",
      "company": {"display_name": "Example B.V."},
      "location": {"display_name": "Nederland", "area": ["Nederland"]}
    }
  ]
}
```

`tests/fixtures/ba_search.json` :
```json
{
  "stellenangebote": [
    {
      "refnr": "10001-1000123456-S",
      "titel": "Praktikant Data Science (m/w/d)",
      "beruf": "Praktikant/in",
      "arbeitgeber": "Zalando SE",
      "arbeitsort": {"ort": "Berlin", "land": "Deutschland"},
      "aktuelleVeroeffentlichungsdatum": "2026-09-20"
    }
  ],
  "maxErgebnisse": 1
}
```

`tests/fixtures/ba_detail.json` :
```json
{
  "stellenangebotsTitel": "Praktikant Data Science (m/w/d)",
  "stellenangebotsBeschreibung": "<p>6 Monate Pflichtpraktikum ab Januar 2027.</p>"
}
```

- [ ] **Step 2 : tests qui échouent**

`tests/test_collectors.py` :
```python
import base64
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from tenacity import wait_none

from stage_radar.collectors import build_collectors
from stage_radar.collectors.adzuna import AdzunaCollector
from stage_radar.collectors.arbeitsagentur import BA_BASE, ArbeitsagenturCollector
from stage_radar.collectors.base import SourceAuthError, get_json, redact

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@respx.mock
def test_get_json_retries_server_errors():
    route = respx.get("https://api.test/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    fast = get_json.retry_with(wait=wait_none())
    assert fast(httpx.Client(), "https://api.test/x") == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_get_json_auth_error_is_not_retried():
    route = respx.get("https://api.test/x").mock(return_value=httpx.Response(401))
    with pytest.raises(SourceAuthError):
        get_json(httpx.Client(), "https://api.test/x")
    assert route.call_count == 1


def test_redact_hides_keys():
    text = "GET https://api.adzuna.com/x?app_id=abc&app_key=secret123 failed"
    assert "secret123" not in redact(text) and "abc" not in redact(text)


@respx.mock
def test_adzuna_maps_results_and_stops_on_short_page():
    route = respx.get("https://api.adzuna.com/v1/api/jobs/nl/search/1").mock(
        return_value=httpx.Response(200, json=load("adzuna_search.json"))
    )
    collector = AdzunaCollector("id", "key", countries=["nl"], queries=["data intern"],
                                extra_queries={}, max_pages=3, results_per_page=50)
    offers = list(collector.fetch(date.today() - timedelta(days=5)))
    assert route.call_count == 1
    first = offers[0]
    assert first.source == "adzuna" and first.source_id == "4811"
    assert first.title == "Data Science Intern (m/f/d)"
    assert first.company == "Adyen" and first.country == "NL" and first.city == "Amsterdam"
    assert first.description_is_full is False
    assert first.posted_at.year == 2026
    assert offers[1].city is None
    params = route.calls[0].request.url.params
    assert params["what"] == "data intern" and params["max_days_old"] == "5"


@respx.mock
def test_adzuna_uses_country_extra_queries():
    route = respx.get(url__regex=r"https://api.adzuna.com/v1/api/jobs/de/search/1.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    collector = AdzunaCollector("id", "key", countries=["de"], queries=["data intern"],
                                extra_queries={"de": ["praktikum data"]}, max_pages=1,
                                results_per_page=50)
    list(collector.fetch(date.today() - timedelta(days=2)))
    assert [c.request.url.params["what"] for c in route.calls] == ["data intern", "praktikum data"]


def test_adzuna_without_keys_raises_auth_error():
    collector = AdzunaCollector("", "", countries=["nl"], queries=["x"], extra_queries={},
                                max_pages=1, results_per_page=50)
    with pytest.raises(SourceAuthError):
        list(collector.fetch(date.today()))


@respx.mock
def test_arbeitsagentur_fetches_full_description():
    respx.get(f"{BA_BASE}/pc/v4/app/jobs").mock(
        return_value=httpx.Response(200, json=load("ba_search.json"))
    )
    code = base64.b64encode(b"10001-1000123456-S").decode()
    respx.get(f"{BA_BASE}/pc/v4/jobdetails/{code}").mock(
        return_value=httpx.Response(200, json=load("ba_detail.json"))
    )
    collector = ArbeitsagenturCollector(queries=["Data Science"], max_pages=1, page_size=50)
    [offer] = list(collector.fetch(date.today() - timedelta(days=3)))
    assert offer.source_id == "10001-1000123456-S"
    assert offer.country == "DE" and offer.city == "Berlin" and offer.company == "Zalando SE"
    assert offer.description == "6 Monate Pflichtpraktikum ab Januar 2027."
    assert offer.description_is_full is True
    assert offer.url.endswith("10001-1000123456-S")


@respx.mock
def test_arbeitsagentur_detail_failure_falls_back_to_snippet():
    respx.get(f"{BA_BASE}/pc/v4/app/jobs").mock(
        return_value=httpx.Response(200, json=load("ba_search.json"))
    )
    respx.get(url__regex=rf"{BA_BASE}/pc/v4/jobdetails/.*").mock(
        return_value=httpx.Response(404)
    )
    collector = ArbeitsagenturCollector(queries=["Data Science"], max_pages=1, page_size=50)
    [offer] = list(collector.fetch(date.today()))
    assert offer.description_is_full is False and offer.description == "Praktikant/in"


def test_build_collectors_respects_enabled_flags():
    cfg = {"adzuna": {"enabled": True, "countries": ["gb"], "queries": ["x"]},
           "arbeitsagentur": {"enabled": False}}
    names = [c.name for c in build_collectors(cfg, {"ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "b"})]
    assert names == ["adzuna"]
```

`tests/test_stage_collect.py` :
```python
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
```

- [ ] **Step 3 : lancer et constater l'échec**

Run : `.venv\Scripts\python -m pytest tests/test_collectors.py tests/test_stage_collect.py -q`
Expected : FAIL (import error)

- [ ] **Step 4 : implémenter**

`src/stage_radar/collectors/base.py` :
```python
"""Contrat commun des collecteurs et client HTTP avec nouvelles tentatives."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from stage_radar.models import RawOffer

USER_AGENT = "stage-radar/0.1 (+https://github.com/Baudet-Antoine)"


class Collector(Protocol):
    name: str

    def fetch(self, since: date) -> Iterator[RawOffer]: ...


class SourceAuthError(Exception):
    """Clé absente ou refusée (401/403) : inutile de réessayer."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def get_json(client: httpx.Client, url: str, **kwargs: Any) -> Any:
    response = client.get(url, **kwargs)
    if response.status_code in (401, 403):
        raise SourceAuthError(f"HTTP {response.status_code} sur {url}")
    response.raise_for_status()
    return response.json()


def new_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


_SECRET_PARAMS = re.compile(r"((?:app_id|app_key|api_key|key|token)=)[^&\s'\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    return _SECRET_PARAMS.sub(r"\1***", text)
```

`src/stage_radar/collectors/adzuna.py` :
```python
"""Collecteur Adzuna (https://developer.adzuna.com). Descriptions tronquées."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import httpx

from stage_radar.collectors.base import SourceAuthError, get_json, new_client
from stage_radar.models import RawOffer
from stage_radar.normalize import parse_dt, strip_html

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaCollector:
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, countries: list[str], queries: list[str],
                 extra_queries: dict[str, list[str]], max_pages: int, results_per_page: int,
                 client: httpx.Client | None = None) -> None:
        self.app_id, self.app_key = app_id, app_key
        self.countries, self.queries, self.extra_queries = countries, queries, extra_queries
        self.max_pages, self.results_per_page = max_pages, results_per_page
        self.client = client or new_client()

    def fetch(self, since: date) -> Iterator[RawOffer]:
        if not (self.app_id and self.app_key):
            raise SourceAuthError("ADZUNA_APP_ID / ADZUNA_APP_KEY manquants")
        max_days_old = max(1, (date.today() - since).days)
        for country in self.countries:
            for query in self.queries + self.extra_queries.get(country, []):
                for page in range(1, self.max_pages + 1):
                    data = get_json(
                        self.client,
                        ADZUNA_URL.format(country=country, page=page),
                        params={
                            "app_id": self.app_id,
                            "app_key": self.app_key,
                            "what": query,
                            "max_days_old": max_days_old,
                            "results_per_page": self.results_per_page,
                            "sort_by": "date",
                            "content-type": "application/json",
                        },
                    )
                    results = data.get("results") or []
                    for item in results:
                        yield self.to_raw(item, country)
                    if len(results) < self.results_per_page:
                        break

    @staticmethod
    def to_raw(item: dict, country: str) -> RawOffer:
        location = item.get("location") or {}
        area = location.get("area") or []
        return RawOffer(
            source="adzuna",
            source_id=str(item["id"]),
            url=item.get("redirect_url", ""),
            title=strip_html(item.get("title")),
            company=(item.get("company") or {}).get("display_name"),
            location_raw=location.get("display_name", ""),
            country=country.upper(),
            city=area[-1] if len(area) >= 2 else None,
            description=strip_html(item.get("description")),
            description_is_full=False,
            posted_at=parse_dt(item.get("created")),
            raw=item,
        )
```

`src/stage_radar/collectors/arbeitsagentur.py` :
```python
"""Collecteur Bundesagentur für Arbeit (https://jobsuche.api.bund.dev), filtre Praktikum."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import date

import httpx

from stage_radar.collectors.base import get_json, new_client
from stage_radar.models import RawOffer
from stage_radar.normalize import parse_dt, strip_html

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_PATH = "/pc/v4/app/jobs"
DETAIL_PATH = "/pc/v4/jobdetails/{code}"
HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
PRAKTIKUM_TRAINEE = 34
COUNTRIES = {"deutschland": "DE", "osterreich": "AT", "österreich": "AT", "schweiz": "CH",
             "niederlande": "NL", "belgien": "BE", "luxemburg": "LU", "frankreich": "FR"}


class ArbeitsagenturCollector:
    name = "arbeitsagentur"

    def __init__(self, queries: list[str], max_pages: int, page_size: int,
                 client: httpx.Client | None = None) -> None:
        self.queries, self.max_pages, self.page_size = queries, max_pages, page_size
        self.client = client or new_client()

    def fetch(self, since: date) -> Iterator[RawOffer]:
        days = min(100, max(1, (date.today() - since).days))
        for query in self.queries:
            for page in range(1, self.max_pages + 1):
                data = get_json(
                    self.client,
                    BA_BASE + SEARCH_PATH,
                    params={"was": query, "angebotsart": PRAKTIKUM_TRAINEE,
                            "veroeffentlichtseit": days, "page": page, "size": self.page_size},
                    headers=HEADERS,
                )
                items = data.get("stellenangebote") or []
                for item in items:
                    yield self.to_raw(item, self._description(item.get("refnr")))
                if len(items) < self.page_size:
                    break

    def _description(self, refnr: str | None) -> str | None:
        if not refnr:
            return None
        code = base64.b64encode(refnr.encode("utf-8")).decode("ascii")
        try:
            detail = get_json(self.client, BA_BASE + DETAIL_PATH.format(code=code),
                              headers=HEADERS)
        except httpx.HTTPError:
            return None
        text = detail.get("stellenangebotsBeschreibung")
        return strip_html(text) if text else None

    @staticmethod
    def to_raw(item: dict, description: str | None) -> RawOffer:
        place = item.get("arbeitsort") or {}
        refnr = item["refnr"]
        land = (place.get("land") or "").strip().lower()
        return RawOffer(
            source="arbeitsagentur",
            source_id=refnr,
            url=item.get("externeUrl")
            or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            title=strip_html(item.get("titel") or item.get("beruf")),
            company=item.get("arbeitgeber"),
            location_raw=", ".join(v for v in (place.get("ort"), place.get("land")) if v),
            country=COUNTRIES.get(land),
            city=place.get("ort"),
            description=description or strip_html(item.get("beruf")),
            description_is_full=description is not None,
            posted_at=parse_dt(item.get("aktuelleVeroeffentlichungsdatum")),
            raw=item,
        )
```

`src/stage_radar/collectors/__init__.py` :
```python
from __future__ import annotations

from collections.abc import Mapping

from stage_radar.collectors.adzuna import AdzunaCollector
from stage_radar.collectors.arbeitsagentur import ArbeitsagenturCollector
from stage_radar.collectors.base import Collector


def build_collectors(search_cfg: dict, env: Mapping[str, str]) -> list[Collector]:
    collectors: list[Collector] = []
    adzuna = search_cfg.get("adzuna") or {}
    if adzuna.get("enabled"):
        collectors.append(AdzunaCollector(
            app_id=env.get("ADZUNA_APP_ID", ""),
            app_key=env.get("ADZUNA_APP_KEY", ""),
            countries=adzuna.get("countries", []),
            queries=adzuna.get("queries", []),
            extra_queries=adzuna.get("extra_queries") or {},
            max_pages=adzuna.get("max_pages", 1),
            results_per_page=adzuna.get("results_per_page", 50),
        ))
    ba = search_cfg.get("arbeitsagentur") or {}
    if ba.get("enabled"):
        collectors.append(ArbeitsagenturCollector(
            queries=ba.get("queries", []),
            max_pages=ba.get("max_pages", 1),
            page_size=ba.get("page_size", 50),
        ))
    return collectors
```

`src/stage_radar/stages/__init__.py` : fichier vide.

`src/stage_radar/stages/collect.py` :
```python
"""Étape collect : sources → offers (statut collected)."""

from __future__ import annotations

from datetime import date

import psycopg

from stage_radar import db
from stage_radar.collectors.base import Collector, redact
from stage_radar.models import RunReport
from stage_radar.normalize import dedup_key


def run_collect(conn: psycopg.Connection, collectors: list[Collector], since: date,
                report: RunReport) -> None:
    for collector in collectors:
        seen = new = 0
        try:
            for raw in collector.fetch(since):
                if not raw.title:
                    continue
                _, is_new = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title,
                                                                 raw.country))
                conn.commit()
                seen += 1
                new += int(is_new)
        except Exception as exc:  # une source en panne ne bloque pas les autres
            conn.rollback()
            report.error(collector.name, redact(f"{type(exc).__name__}: {exc}"))
        report.count("collect", f"{collector.name}_seen", seen)
        report.count("collect", f"{collector.name}_new", new)
```

- [ ] **Step 5 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_collectors.py tests/test_stage_collect.py -q` → PASS
- [ ] **Step 6 : commit** — `git add -A && git commit -m "feat: Adzuna and Arbeitsagentur collectors, collect stage"`

---

### Task 4 : fenêtre visa, règles et étape prefilter

**Files :**
- Create : `src/stage_radar/visa.py`, `src/stage_radar/rules.py`, `src/stage_radar/stages/prefilter.py`
- Test : `tests/test_rules.py`

**Interfaces :**
- Consumes : `Country`, `normalize_title`, `db.fetch_offers/update_offer/reject`.
- Produces : `visa.apply_deadline(country, latest_start, recruitment_weeks) -> date` ; `rules.Rules.from_config(cfg)` ; `rules.Verdict(passed, code, reason)` ; `rules.evaluate(offer: dict, rules, countries, today) -> Verdict` (clés lues : `country, title, posted_at`) ; `stages.prefilter.run_prefilter(conn, rules, countries, today, report)`. Codes de rejet : `country`, `title_data`, `title_internship`, `title_blacklist`, `too_old`, `visa_window`.

- [ ] **Step 1 : tests qui échouent** — `tests/test_rules.py` :
```python
from datetime import UTC, date, datetime

from stage_radar.config import Country, load_countries, load_yaml
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.rules import Rules, evaluate
from stage_radar.stages.prefilter import run_prefilter
from stage_radar.visa import apply_deadline

RULES = Rules.from_config(load_yaml("rules.yaml"))
COUNTRIES = load_countries()
TODAY = date(2026, 10, 1)


def offer(title="Data Science Intern", country="DE", posted=datetime(2026, 9, 28, tzinfo=UTC)):
    return {"title": title, "country": country, "posted_at": posted}


def test_apply_deadline():
    us = Country("US", "États-Unis", 12, 2200, True)
    assert apply_deadline(us, date(2027, 3, 1), 4) == date(2026, 11, 9)
    de = Country("DE", "Allemagne", 0, 1100, True)
    assert apply_deadline(de, date(2027, 3, 1), 4) == date(2027, 2, 1)


def test_valid_offer_passes():
    assert evaluate(offer(), RULES, COUNTRIES, TODAY).passed


def test_german_compound_praktikum_passes():
    assert evaluate(offer("Pflichtpraktikum Data Analytics (m/w/d)"), RULES, COUNTRIES, TODAY).passed


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
```

- [ ] **Step 2 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_rules.py -q` → FAIL

- [ ] **Step 3 : implémenter**

`src/stage_radar/visa.py` :
```python
from __future__ import annotations

from datetime import date, timedelta

from stage_radar.config import Country


def apply_deadline(country: Country, latest_start: date, recruitment_weeks: int) -> date:
    """Dernier jour réaliste pour postuler : démarrage max − délai visa − recrutement."""
    return latest_start - timedelta(weeks=country.visa_lead_weeks + recruitment_weeks)
```

`src/stage_radar/rules.py` :
```python
"""Étage A : règles déterministes appliquées avant tout modèle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from stage_radar.config import Country
from stage_radar.normalize import normalize_title
from stage_radar.visa import apply_deadline


@dataclass(frozen=True)
class Rules:
    latest_start: date
    recruitment_weeks: int
    max_age_days: int
    excluded_countries: frozenset[str]
    data: tuple[re.Pattern, ...]
    internship: tuple[re.Pattern, ...]
    blacklist: tuple[re.Pattern, ...]

    @classmethod
    def from_config(cls, cfg: dict) -> Rules:
        def compile_all(key: str) -> tuple[re.Pattern, ...]:
            return tuple(re.compile(p) for p in cfg.get(key, []))

        return cls(
            latest_start=cfg["latest_start"],
            recruitment_weeks=int(cfg["recruitment_weeks"]),
            max_age_days=int(cfg["max_age_days"]),
            excluded_countries=frozenset(c.upper() for c in cfg.get("excluded_countries", [])),
            data=compile_all("title_data_patterns"),
            internship=compile_all("title_internship_patterns"),
            blacklist=compile_all("title_blacklist_patterns"),
        )


@dataclass(frozen=True)
class Verdict:
    passed: bool
    code: str | None = None
    reason: str | None = None


def _reject(code: str, reason: str) -> Verdict:
    return Verdict(False, code, reason)


def evaluate(offer: dict, rules: Rules, countries: dict[str, Country], today: date) -> Verdict:
    code = (offer.get("country") or "").upper()
    if not code:
        return _reject("country", "pays inconnu")
    if code in rules.excluded_countries:
        return _reject("country", f"pays exclu ({code})")
    country = countries.get(code)
    if country is None or not country.in_scope:
        return _reject("country", f"pays hors périmètre ({code})")

    title = normalize_title(offer.get("title"))
    if not any(p.search(title) for p in rules.data):
        return _reject("title_data", "titre sans terme data")
    if not any(p.search(title) for p in rules.internship):
        return _reject("title_internship", "titre sans terme stage")
    for pattern in rules.blacklist:
        match = pattern.search(title)
        if match:
            return _reject("title_blacklist", f"titre exclu ({match.group(0)})")

    posted = offer.get("posted_at")
    if posted is not None:
        age = (today - posted.date()).days
        if age > rules.max_age_days:
            return _reject("too_old", f"offre trop ancienne ({age} j)")

    deadline = apply_deadline(country, rules.latest_start, rules.recruitment_weeks)
    if today > deadline:
        return _reject("visa_window",
                       f"fenêtre visa fermée ({code}, limite {deadline.isoformat()})")
    return Verdict(True)
```

`src/stage_radar/stages/prefilter.py` :
```python
"""Étape prefilter : collected → prefiltered | rejected."""

from __future__ import annotations

from datetime import date

import psycopg

from stage_radar import db
from stage_radar.config import Country
from stage_radar.models import OfferStatus, RunReport
from stage_radar.rules import Rules, evaluate


def run_prefilter(conn: psycopg.Connection, rules: Rules, countries: dict[str, Country],
                  today: date, report: RunReport) -> None:
    for offer in db.fetch_offers(conn, [OfferStatus.COLLECTED]):
        verdict = evaluate(offer, rules, countries, today)
        if verdict.passed:
            db.update_offer(conn, offer["id"], status=OfferStatus.PREFILTERED)
            report.count("prefilter", "passed")
        else:
            db.reject(conn, offer["id"], "prefilter", verdict.reason)
            report.count("prefilter", verdict.code)
        conn.commit()
```

- [ ] **Step 4 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_rules.py -q` → PASS
- [ ] **Step 5 : commit** — `git add -A && git commit -m "feat: deterministic prefilter with visa window"`

---

### Task 5 : moteurs de décision (base, fake, Gemini), zones et étape classify

**Files :**
- Create : `src/stage_radar/engines/{__init__,base,fake,gemini}.py`, `src/stage_radar/stages/classify.py`
- Test : `tests/test_engines.py`, `tests/test_stage_classify.py`

**Interfaces :**
- Consumes : `db.*`, `RunReport`, `SourceAuthError`, `redact`.
- Produces : `engines.base.Question(id,label,kind,prompt,choices,reject_answers,flag_answers,include_profile)` avec `.answers` ; `Decision(answer: str, p: float)` ; `DecisionEngine` (Protocol `name`, `classify(text, profile, questions) -> dict[str, Decision]`) ; `load_questions(cfg) -> list[Question]` ; `engine_version(name, cfg) -> str` ; `offer_text(offer: dict, max_chars=8000) -> str` ; `engines.fake.FakeEngine(answers=None)` ; `engines.gemini.GeminiClient(api_key, model, min_interval_s=0.0, client=None, sleep=time.sleep).generate_json(system, prompt, schema) -> dict` ; `GeminiQuotaError` ; `GeminiEngine(client)` ; `stages.classify.Outcome(rejected, code, reason, flags)` ; `apply_zones(questions, decisions, reject_threshold, uncertain_below) -> Outcome` ; `run_classify(conn, engine, questions, profile, thresholds: tuple[float, float], version, report)`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_engines.py` :
```python
import json

import httpx
import pytest
import respx

from stage_radar.config import load_yaml
from stage_radar.engines.base import Decision, engine_version, load_questions, offer_text
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import GEMINI_URL, GeminiClient, GeminiEngine, GeminiQuotaError
from stage_radar.stages.classify import apply_zones

QUESTIONS = load_questions(load_yaml("decisions.yaml"))
Q = {q.id: q for q in QUESTIONS}


def test_load_questions_answers():
    assert Q["is_internship_convention"].answers == ("yes", "no")
    assert Q["profile_fit"].answers == ("1", "2", "3", "4", "5")
    assert Q["duration"].reject_answers == ("lt4", "4to5")
    assert Q["profile_fit"].include_profile is True


def test_engine_version_changes_with_config():
    assert engine_version("gemini", {"a": 1}) != engine_version("gemini", {"a": 2})


def test_offer_text_truncates():
    text = offer_text({"title": "T", "company": "C", "city": "Berlin", "country": "DE",
                       "description": "x" * 10000}, max_chars=100)
    assert text.startswith("Title: T") and len(text) < 200


def test_fake_engine_defaults_pass_everything():
    decisions = FakeEngine().classify("t", "p", QUESTIONS)
    assert apply_zones(QUESTIONS, decisions, 0.85, 0.6).rejected is False


def test_zones_reject_flag_and_pass():
    base = FakeEngine().classify("t", "p", QUESTIONS)
    remote = {**base, "work_mode": Decision("remote", 0.9)}
    out = apply_zones(QUESTIONS, remote, 0.85, 0.6)
    assert out.rejected and out.code == "work_mode" and "p=0.90" in out.reason

    unsure_remote = {**base, "work_mode": Decision("remote", 0.7)}
    out = apply_zones(QUESTIONS, unsure_remote, 0.85, 0.6)
    assert not out.rejected and out.flags == ["mode de travail : remote ? (p=0.70)"]

    unspecified = {**base, "duration": Decision("unspecified", 0.9)}
    out = apply_zones(QUESTIONS, unspecified, 0.85, 0.6)
    assert out.flags == ["durée non précisée"]

    low_conf = {**base, "is_internship_convention": Decision("yes", 0.5)}
    assert apply_zones(QUESTIONS, low_conf, 0.85, 0.6).flags == ["stage sous convention incertain"]

    missing = {k: v for k, v in base.items() if k != "start"}
    assert apply_zones(QUESTIONS, missing, 0.85, 0.6).flags == ["démarrage : pas de réponse"]


def gemini_response(payload: dict) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    return httpx.Response(200, json=body)


@respx.mock
def test_gemini_client_parses_json_and_sends_schema():
    route = respx.post(GEMINI_URL.format(model="m")).mock(return_value=gemini_response({"a": 1}))
    client = GeminiClient("key", "m", sleep=lambda s: None)
    assert client.generate_json("sys", "prompt", {"type": "OBJECT"}) == {"a": 1}
    sent = json.loads(route.calls[0].request.content)
    assert sent["generationConfig"]["responseSchema"] == {"type": "OBJECT"}
    assert route.calls[0].request.headers["x-goog-api-key"] == "key"


@respx.mock
def test_gemini_client_quota_error_after_retries():
    respx.post(GEMINI_URL.format(model="m")).mock(return_value=httpx.Response(429))
    sleeps = []
    client = GeminiClient("key", "m", sleep=sleeps.append)
    with pytest.raises(GeminiQuotaError):
        client.generate_json("sys", "prompt", {})
    assert len(sleeps) == 2


@respx.mock
def test_gemini_engine_maps_confidence_and_drops_invalid_answers():
    payload = {q.id: {"answer": q.answers[0], "confidence": "high"} for q in QUESTIONS}
    payload["duration"] = {"answer": "7 months", "confidence": "high"}
    payload["work_mode"] = {"answer": "hybrid", "confidence": "medium"}
    respx.post(GEMINI_URL.format(model="m")).mock(return_value=gemini_response(payload))
    engine = GeminiEngine(GeminiClient("key", "m", sleep=lambda s: None))
    decisions = engine.classify("offer", "profile", QUESTIONS)
    assert "duration" not in decisions
    assert decisions["work_mode"] == Decision("hybrid", 0.7)
    assert decisions["is_internship_convention"] == Decision("yes", 0.9)
```

`tests/test_stage_classify.py` :
```python
from datetime import UTC, datetime

from stage_radar import db
from stage_radar.config import load_yaml
from stage_radar.engines.base import Decision, load_questions
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import GeminiQuotaError
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


def test_run_classify(conn):
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
```

- [ ] **Step 2 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_engines.py tests/test_stage_classify.py -q` → FAIL

- [ ] **Step 3 : implémenter**

`src/stage_radar/engines/__init__.py` : fichier vide.

`src/stage_radar/engines/base.py` :
```python
"""Interface commune des moteurs de décision typés."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

SCORE_ANSWERS = ("1", "2", "3", "4", "5")


@dataclass(frozen=True)
class Question:
    id: str
    label: str
    kind: Literal["bool", "choice", "score"]
    prompt: str
    choices: tuple[str, ...] = ()
    reject_answers: tuple[str, ...] = ()
    flag_answers: tuple[str, ...] = ()
    include_profile: bool = False
    flag_text: str | None = None

    @property
    def answers(self) -> tuple[str, ...]:
        if self.kind == "bool":
            return ("yes", "no")
        if self.kind == "score":
            return SCORE_ANSWERS
        return self.choices


@dataclass(frozen=True)
class Decision:
    answer: str
    p: float


class DecisionEngine(Protocol):
    name: str

    def classify(self, text: str, profile: str,
                 questions: list[Question]) -> dict[str, Decision]: ...


def load_questions(cfg: dict) -> list[Question]:
    return [
        Question(
            id=q["id"],
            label=q.get("label", q["id"]),
            kind=q["kind"],
            prompt=q.get("prompt", ""),
            choices=tuple(str(c) for c in q.get("choices", [])),
            reject_answers=tuple(str(a) for a in q.get("reject_answers", [])),
            flag_answers=tuple(str(a) for a in q.get("flag_answers", [])),
            include_profile=bool(q.get("include_profile", False)),
            flag_text=q.get("flag_text"),
        )
        for q in cfg.get("questions", [])
    ]


def engine_version(engine_name: str, cfg: dict) -> str:
    payload = json.dumps({"engine": engine_name, "cfg": cfg}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def offer_text(offer: dict, max_chars: int = 8000) -> str:
    location = ", ".join(v for v in (offer.get("city"), offer.get("country")) if v)
    return (
        f"Title: {offer.get('title', '')}\n"
        f"Company: {offer.get('company') or 'unknown'}\n"
        f"Location: {location or 'unknown'}\n\n"
        f"{(offer.get('description') or '')[:max_chars]}"
    )
```

`src/stage_radar/engines/fake.py` :
```python
"""Moteur de test : répond « tout passe » sauf réponses imposées."""

from __future__ import annotations

from stage_radar.engines.base import Decision, Question


def _default_answer(question: Question) -> str:
    if question.kind == "score":
        return "3"
    blocked = set(question.reject_answers) | set(question.flag_answers)
    return next(a for a in question.answers if a not in blocked)


class FakeEngine:
    name = "fake"

    def __init__(self, answers: dict[str, Decision] | None = None) -> None:
        self.answers = answers or {}

    def classify(self, text: str, profile: str,
                 questions: list[Question]) -> dict[str, Decision]:
        return {q.id: self.answers.get(q.id, Decision(_default_answer(q), 0.9))
                for q in questions}
```

`src/stage_radar/engines/gemini.py` :
```python
"""Client REST Gemini (sortie JSON contrainte par schéma) et moteur de décision associé."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from stage_radar.collectors.base import SourceAuthError
from stage_radar.engines.base import Decision, Question

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CONFIDENCE_P = {"high": 0.9, "medium": 0.7, "low": 0.5}


class GeminiQuotaError(Exception):
    """Quota (429) toujours dépassé après nouvelles tentatives : on s'arrête pour aujourd'hui."""


class GeminiClient:
    def __init__(self, api_key: str, model: str, min_interval_s: float = 0.0,
                 client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 3) -> None:
        self.api_key, self.model = api_key, model
        self.min_interval_s, self.sleep, self.max_attempts = min_interval_s, sleep, max_attempts
        self.http = client or httpx.Client(timeout=90)
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        self._last_call = time.monotonic()

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        url = GEMINI_URL.format(model=self.model)
        for attempt in range(1, self.max_attempts + 1):
            last = attempt == self.max_attempts
            self._throttle()
            try:
                response = self.http.post(url, json=body,
                                          headers={"x-goog-api-key": self.api_key})
            except httpx.TransportError:
                if last:
                    raise
                self.sleep(5 * attempt)
                continue
            if response.status_code == 429:
                if last:
                    raise GeminiQuotaError("quota Gemini atteint (HTTP 429)")
                self.sleep(30)
                continue
            if response.status_code in (401, 403):
                raise SourceAuthError(f"Gemini HTTP {response.status_code}")
            if response.status_code >= 500 and not last:
                self.sleep(5 * attempt)
                continue
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        raise RuntimeError("unreachable")


SYSTEM = (
    "You screen internship offers for a student. Answer every question using ONLY the offer "
    "text. When the information is not stated and an 'unspecified' answer exists, choose it. "
    "Give your confidence: high, medium or low."
)


class GeminiEngine:
    name = "gemini"

    def __init__(self, client: GeminiClient) -> None:
        self.client = client

    def classify(self, text: str, profile: str,
                 questions: list[Question]) -> dict[str, Decision]:
        schema = {
            "type": "OBJECT",
            "properties": {
                q.id: {
                    "type": "OBJECT",
                    "properties": {
                        "answer": {"type": "STRING", "enum": list(q.answers)},
                        "confidence": {"type": "STRING", "enum": list(CONFIDENCE_P)},
                    },
                    "required": ["answer", "confidence"],
                }
                for q in questions
            },
            "required": [q.id for q in questions],
        }
        lines = [f"- {q.id}: {q.prompt} Allowed answers: {', '.join(q.answers)}."
                 for q in questions]
        prompt = f"OFFER:\n<<<\n{text}\n>>>\n\n"
        if any(q.include_profile for q in questions):
            prompt += f"CANDIDATE PROFILE:\n<<<\n{profile}\n>>>\n\n"
        prompt += "QUESTIONS:\n" + "\n".join(lines)
        data = self.client.generate_json(SYSTEM, prompt, schema)
        decisions = {}
        for q in questions:
            item = data.get(q.id) or {}
            answer = str(item.get("answer", ""))
            if answer in q.answers:
                decisions[q.id] = Decision(answer, CONFIDENCE_P.get(item.get("confidence"), 0.5))
        return decisions
```

`src/stage_radar/stages/classify.py` :
```python
"""Étape classify : prefiltered → classified | rejected, via un DecisionEngine."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from stage_radar import db
from stage_radar.collectors.base import SourceAuthError, redact
from stage_radar.engines.base import Decision, DecisionEngine, Question, offer_text
from stage_radar.engines.gemini import GeminiQuotaError
from stage_radar.models import OfferStatus, RunReport


@dataclass
class Outcome:
    rejected: bool
    code: str | None = None
    reason: str | None = None
    flags: list[str] = field(default_factory=list)


def apply_zones(questions: list[Question], decisions: dict[str, Decision],
                reject_threshold: float, uncertain_below: float) -> Outcome:
    flags: list[str] = []
    for q in questions:
        d = decisions.get(q.id)
        if d is None:
            flags.append(f"{q.label} : pas de réponse")
        elif d.answer in q.reject_answers:
            if d.p >= reject_threshold:
                return Outcome(True, q.id, f"{q.label} = {d.answer} (p={d.p:.2f})", flags)
            flags.append(f"{q.label} : {d.answer} ? (p={d.p:.2f})")
        elif d.answer in q.flag_answers:
            flags.append(q.flag_text or f"{q.label} non précisé")
        elif q.reject_answers and d.p < uncertain_below:
            flags.append(f"{q.label} incertain")
    return Outcome(False, flags=flags)


def run_classify(conn: psycopg.Connection, engine: DecisionEngine, questions: list[Question],
                 profile: str, thresholds: tuple[float, float], version: str,
                 report: RunReport) -> None:
    reject_threshold, uncertain_below = thresholds
    for offer in db.fetch_offers(conn, [OfferStatus.PREFILTERED]):
        try:
            decisions = engine.classify(offer_text(offer), profile, questions)
        except (GeminiQuotaError, SourceAuthError) as exc:
            report.error("classify", redact(str(exc)))
            break
        except Exception as exc:  # une offre problématique ne bloque pas les suivantes
            report.error("classify", redact(f"{type(exc).__name__}: {exc}"))
            continue
        outcome = apply_zones(questions, decisions, reject_threshold, uncertain_below)
        payload = {k: {"answer": d.answer, "p": d.p} for k, d in decisions.items()}
        if outcome.rejected:
            db.reject(conn, offer["id"], "classify", outcome.reason, decisions=payload,
                      engine_version=version)
            report.count("classify", f"rejected_{outcome.code}")
        else:
            db.update_offer(conn, offer["id"], status=OfferStatus.CLASSIFIED, decisions=payload,
                            flags=outcome.flags, engine_version=version)
            report.count("classify", "passed")
        conn.commit()
```

Note : le texte d'un flag « non précisé » vient de `flag_text` dans `decisions.yaml` (accord grammatical explicite), avec `"<label> non précisé"` par défaut.

- [ ] **Step 4 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_engines.py tests/test_stage_classify.py -q` → PASS
- [ ] **Step 5 : commit** — `git add -A && git commit -m "feat: typed decision engines (fake, Gemini) and classify stage"`

---

### Task 6 : salaire, change et étape enrich

**Files :**
- Create : `src/stage_radar/salary.py`, `src/stage_radar/stages/enrich.py`
- Test : `tests/test_salary.py`, `tests/test_stage_enrich.py`

**Interfaces :**
- Consumes : `GeminiClient.generate_json`, `GeminiQuotaError`, `offer_text`, `db.fetch_to_enrich/update_offer`.
- Produces : `salary.find_salary(text) -> tuple[float, str, str] | None` ; `to_monthly_eur(amount, currency, period, rates) -> float | None` ; `plausible(eur) -> bool` ; `fetch_rates(client=None) -> dict[str, float]` (base EUR, secours `FALLBACK_RATES`) ; `stages.enrich.Extraction` ; `finalize(ex, description, rates) -> tuple[dict, str]` ; `run_enrich(conn, client, rates, report)`. Clés de `extracted` : `salary_eur_month, salary_source, start_date, duration_months, city, key_requirements, warnings`.

- [ ] **Step 1 : tests qui échouent**

`tests/test_salary.py` :
```python
import httpx
import respx

from stage_radar.salary import FALLBACK_RATES, FX_URL, fetch_rates, find_salary, plausible, to_monthly_eur

RATES = {"GBP": 0.85, "CHF": 0.95}


def test_find_salary_variants():
    assert find_salary("We pay €1,500/month gross") == (1500.0, "EUR", "month")
    assert find_salary("Vergütung: 1.200 € brutto pro Monat") == (1200.0, "EUR", "month")
    assert find_salary("£24,000 per year") == (24000.0, "GBP", "year")
    assert find_salary("CHF 2400 per month") == (2400.0, "CHF", "month")
    assert find_salary("€15 per hour") == (15.0, "EUR", "hour")
    assert find_salary("6 months internship") is None


def test_to_monthly_eur():
    assert to_monthly_eur(1500, "EUR", "month", RATES) == 1500
    assert to_monthly_eur(24000, "GBP", "year", RATES) == 24000 / 12 / 0.85
    assert to_monthly_eur(15, "EUR", "hour", RATES) == 2400
    assert to_monthly_eur(100, "XYZ", "month", RATES) is None


def test_plausible():
    assert plausible(1200) and not plausible(150) and not plausible(9000)


@respx.mock
def test_fetch_rates_falls_back():
    respx.get(FX_URL).mock(return_value=httpx.Response(500))
    assert fetch_rates(httpx.Client()) == FALLBACK_RATES
```

`tests/test_stage_enrich.py` :
```python
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
```

- [ ] **Step 2 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_salary.py tests/test_stage_enrich.py -q` → FAIL

- [ ] **Step 3 : implémenter**

`src/stage_radar/salary.py` :
```python
"""Extraction de salaire par regex, conversion en EUR mensuel, plausibilité."""

from __future__ import annotations

import re

import httpx

FX_URL = "https://api.frankfurter.app/latest"
FALLBACK_RATES = {"GBP": 0.85, "CHF": 0.94, "USD": 1.10, "CAD": 1.50, "AUD": 1.65,
                  "SGD": 1.45, "SEK": 11.3, "DKK": 7.46, "NOK": 11.6, "PLN": 4.3, "CZK": 25.0}
MIN_EUR, MAX_EUR = 300, 6000
HOURS_PER_MONTH = 160

_CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP",
               "gbp": "GBP", "chf": "CHF", "$": "USD", "usd": "USD", "sek": "SEK",
               "dkk": "DKK", "nok": "NOK", "pln": "PLN"}
_CUR = r"€|£|\$|\b(?:eur|euros?|gbp|chf|usd|sek|dkk|nok|pln)\b"
_PATTERN = re.compile(
    rf"(?P<c1>{_CUR})?\s*"
    r"(?P<num>\d{1,3}(?:[.,  ]\d{3})+|\d{1,6})(?:[.,]\d{1,2})?\s*"
    rf"(?P<c2>{_CUR})?\s*"
    r"(?:brutto|gross|bruts?|netto|net)?\s*(?:/|per|pro|par|a|an|al|each)?\s*"
    r"(?P<period>month|monat|mois|mes|mese|year|jahr|annum|hour|stunde|heure)\w*",
    re.IGNORECASE,
)


def _period(word: str) -> str:
    word = word.lower()
    if word.startswith(("year", "jahr", "annum")):
        return "year"
    if word.startswith(("hour", "stunde", "heure")):
        return "hour"
    return "month"


def find_salary(text: str) -> tuple[float, str, str] | None:
    for match in _PATTERN.finditer(text or ""):
        currency = match.group("c1") or match.group("c2")
        if not currency:
            continue
        amount = float(re.sub(r"[.,  ]", "", match.group("num")))
        return amount, _CURRENCIES[currency.lower()], _period(match.group("period"))
    return None


def to_monthly_eur(amount: float, currency: str, period: str,
                   rates: dict[str, float]) -> float | None:
    monthly = {"month": amount, "year": amount / 12, "hour": amount * HOURS_PER_MONTH}[period]
    currency = currency.upper()
    if currency == "EUR":
        return monthly
    rate = rates.get(currency)
    return monthly / rate if rate else None


def plausible(eur_per_month: float) -> bool:
    return MIN_EUR <= eur_per_month <= MAX_EUR


def fetch_rates(client: httpx.Client | None = None) -> dict[str, float]:
    """Taux BCE (1 EUR = x devise) via Frankfurter ; valeurs de secours si indisponible."""
    try:
        response = (client or httpx.Client(timeout=20)).get(FX_URL, params={"from": "EUR"})
        response.raise_for_status()
        return {**FALLBACK_RATES, **response.json()["rates"]}
    except (httpx.HTTPError, KeyError, ValueError):
        return dict(FALLBACK_RATES)
```

`src/stage_radar/stages/enrich.py` :
```python
"""Étape enrich : extraction structurée (Gemini) + garde-fous."""

from __future__ import annotations

import re
from typing import Literal, Protocol

import psycopg
from pydantic import BaseModel

from stage_radar import db
from stage_radar.collectors.base import SourceAuthError, redact
from stage_radar.engines.base import offer_text
from stage_radar.engines.gemini import GeminiQuotaError
from stage_radar.models import OfferStatus, RunReport
from stage_radar.salary import find_salary, plausible, to_monthly_eur

START_MIN, START_MAX = "2026-10", "2027-09"


class Extraction(BaseModel):
    salary_amount: float | None = None
    salary_currency: str | None = None
    salary_period: Literal["month", "year", "hour"] | None = None
    start_date: str | None = None
    duration_months: int | None = None
    city: str | None = None
    summary_fr: str = ""
    key_requirements: list[str] = []


def _nullable(kind: str, **extra) -> dict:
    return {"type": kind, "nullable": True, **extra}


EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "salary_amount": _nullable("NUMBER"),
        "salary_currency": _nullable("STRING"),
        "salary_period": _nullable("STRING", enum=["month", "year", "hour"]),
        "start_date": _nullable("STRING"),
        "duration_months": _nullable("INTEGER"),
        "city": _nullable("STRING"),
        "summary_fr": {"type": "STRING"},
        "key_requirements": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary_fr", "key_requirements"],
}

SYSTEM = (
    "Extract facts from an internship offer. Use null for anything not explicitly written in "
    "the offer; never guess. salary_currency is an ISO code (EUR, GBP, CHF...). start_date is "
    "YYYY-MM. summary_fr: two short sentences in French describing the mission and the tech "
    "stack. key_requirements: at most 5 short skills."
)


class JsonClient(Protocol):
    def generate_json(self, system: str, prompt: str, schema: dict) -> dict: ...


def finalize(ex: Extraction, description: str, rates: dict[str, float]) -> tuple[dict, str]:
    warnings: list[str] = []
    amount, currency, period, source = (ex.salary_amount, ex.salary_currency,
                                        ex.salary_period, "llm")
    if amount is None:
        found = find_salary(description)
        if found:
            amount, currency, period = found
            source = "regex"
    eur = None
    if amount is not None:
        eur = to_monthly_eur(amount, currency or "EUR", period or "month", rates)
        if eur is not None and not plausible(eur):
            warnings.append(f"salaire implausible ignoré ({amount} {currency}/{period})")
            eur = None

    start = ex.start_date
    if start is not None and not (re.fullmatch(r"\d{4}-\d{2}", start)
                                  and START_MIN <= start <= START_MAX):
        warnings.append(f"date de début hors fenêtre ignorée ({start})")
        start = None
    duration = ex.duration_months
    if duration is not None and not 1 <= duration <= 24:
        warnings.append(f"durée implausible ignorée ({duration})")
        duration = None

    extracted = {
        "salary_eur_month": round(eur) if eur is not None else None,
        "salary_source": source if eur is not None else None,
        "start_date": start,
        "duration_months": duration,
        "city": ex.city,
        "key_requirements": ex.key_requirements[:5],
        "warnings": warnings,
    }
    return extracted, ex.summary_fr.strip()


def run_enrich(conn: psycopg.Connection, client: JsonClient, rates: dict[str, float],
               report: RunReport) -> None:
    for offer in db.fetch_to_enrich(conn):
        try:
            data = client.generate_json(SYSTEM, offer_text(offer), EXTRACTION_SCHEMA)
            extraction = Extraction.model_validate(data)
        except (GeminiQuotaError, SourceAuthError) as exc:
            report.error("enrich", redact(str(exc)))
            break
        except Exception as exc:  # réponse invalide : on passe à l'offre suivante
            report.error("enrich", redact(f"{type(exc).__name__}: {exc}"))
            continue
        extracted, summary = finalize(extraction, offer["description"], rates)
        fields = {"extracted": extracted, "summary": summary,
                  "city": offer["city"] or extracted["city"]}
        if offer["status"] == OfferStatus.CLASSIFIED:
            fields["status"] = OfferStatus.ENRICHED
        db.update_offer(conn, offer["id"], **fields)
        conn.commit()
        report.count("enrich", "enriched")
```

- [ ] **Step 4 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_salary.py tests/test_stage_enrich.py -q` → PASS
- [ ] **Step 5 : commit** — `git add -A && git commit -m "feat: salary parsing, FX conversion and enrich stage"`

---

### Task 7 : scoring

**Files :** Create `src/stage_radar/scoring.py` ; Test `tests/test_scoring.py`

**Interfaces :**
- Consumes : `Country`, `visa.apply_deadline`, `normalize_text`.
- Produces : `finance_badge(salary_eur_month, cost) -> str` (`profit|even|unknown|deficit`) ; `cost_of_living(city, country, countries, city_costs) -> int | None` ; `ScoreInput(profile_fit, posted_at, deadline, badge, n_flags)` ; `compute(inp, cfg, today) -> tuple[float, dict]` ; `explain(inp, breakdown, country_code, today) -> str`.

- [ ] **Step 1 : tests qui échouent** — `tests/test_scoring.py` :
```python
from datetime import UTC, date, datetime

from stage_radar.config import load_city_costs, load_countries, load_yaml
from stage_radar.scoring import ScoreInput, compute, cost_of_living, explain, finance_badge

CFG = load_yaml("scoring.yaml")
TODAY = date(2026, 10, 1)


def test_finance_badge():
    assert finance_badge(None, 1400) == "unknown"
    assert finance_badge(1700, 1400) == "profit"
    assert finance_badge(1500, 1400) == "even"
    assert finance_badge(900, 1400) == "deficit"
    assert finance_badge(1500, None) == "unknown"


def test_cost_of_living_prefers_city():
    countries, cities = load_countries(), load_city_costs()
    assert cost_of_living("London", "GB", countries, cities) == 2000
    assert cost_of_living("Leeds", "GB", countries, cities) == 1500
    assert cost_of_living(None, "XX", countries, cities) is None


def test_compute_perfect_offer_scores_100():
    inp = ScoreInput(profile_fit=5, posted_at=datetime(2026, 10, 1, tzinfo=UTC),
                     deadline=date(2026, 10, 15), badge="profit", n_flags=0)
    score, parts = compute(inp, CFG, TODAY)
    assert score == 100.0 and parts["urgency"] == 1.0


def test_compute_components():
    inp = ScoreInput(profile_fit=3, posted_at=datetime(2026, 9, 24, tzinfo=UTC),
                     deadline=date(2027, 2, 1), badge="unknown", n_flags=2)
    score, parts = compute(inp, CFG, TODAY)
    assert parts == {"fit": 0.5, "freshness": 0.5, "urgency": 0.0, "finance": 0.5,
                     "certainty": 0.5}
    assert score == 42.5


def test_explain():
    inp = ScoreInput(5, datetime(2026, 9, 30, tzinfo=UTC), date(2026, 10, 19), "profit", 0)
    _, parts = compute(inp, CFG, TODAY)
    text = explain(inp, parts, "GB", TODAY)
    assert text == "profil 5/5 · publiée hier · fenêtre GB se ferme dans 18 j · salaire > coût de la vie"
```

- [ ] **Step 2 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_scoring.py -q` → FAIL

- [ ] **Step 3 : implémenter** `src/stage_radar/scoring.py` :
```python
"""Score 0-100 pour le classement (jamais éliminatoire)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from stage_radar.config import Country
from stage_radar.normalize import normalize_text

BADGE_VALUE = {"profit": 1.0, "even": 0.7, "unknown": 0.5, "deficit": 0.2}
BALANCE_MARGIN = 200


def finance_badge(salary_eur_month: float | None, cost: int | None) -> str:
    if salary_eur_month is None or cost is None:
        return "unknown"
    balance = salary_eur_month - cost
    if balance > BALANCE_MARGIN:
        return "profit"
    if balance < -BALANCE_MARGIN:
        return "deficit"
    return "even"


def cost_of_living(city: str | None, country: str | None, countries: dict[str, Country],
                   city_costs: dict[str, int]) -> int | None:
    if city and normalize_text(city) in city_costs:
        return city_costs[normalize_text(city)]
    c = countries.get((country or "").upper())
    return c.cost_of_living_eur if c else None


@dataclass(frozen=True)
class ScoreInput:
    profile_fit: int | None
    posted_at: datetime | None
    deadline: date | None
    badge: str
    n_flags: int


def compute(inp: ScoreInput, cfg: dict, today: date) -> tuple[float, dict]:
    fit = ((inp.profile_fit or 3) - 1) / 4
    if inp.posted_at is None:
        freshness = 0.5
    else:
        age = max(0, (today - inp.posted_at.date()).days)
        freshness = max(0.0, 1 - age / cfg["freshness_days"])
    urgency = 0.0
    if inp.deadline is not None:
        days = (inp.deadline - today).days
        full, zero = cfg["urgency_full_days"], cfg["urgency_zero_days"]
        urgency = 1.0 if days <= full else max(0.0, 1 - (days - full) / (zero - full))
    parts = {
        "fit": fit,
        "freshness": freshness,
        "urgency": urgency,
        "finance": BADGE_VALUE[inp.badge],
        "certainty": max(0.0, 1 - 0.25 * inp.n_flags),
    }
    weights = cfg["weights"]
    score = 100 * sum(weights[k] * v for k, v in parts.items())
    return round(score, 1), {k: round(v, 3) for k, v in parts.items()}


def explain(inp: ScoreInput, parts: dict, country_code: str | None, today: date) -> str:
    reasons = []
    if inp.profile_fit and inp.profile_fit >= 4:
        reasons.append(f"profil {inp.profile_fit}/5")
    if inp.posted_at is not None:
        age = (today - inp.posted_at.date()).days
        if age <= 0:
            reasons.append("publiée aujourd'hui")
        elif age == 1:
            reasons.append("publiée hier")
        elif age <= 7:
            reasons.append(f"publiée il y a {age} j")
    if inp.deadline is not None and parts["urgency"] >= 0.5:
        reasons.append(f"fenêtre {country_code} se ferme dans {(inp.deadline - today).days} j")
    if inp.badge == "profit":
        reasons.append("salaire > coût de la vie")
    return " · ".join(reasons)
```

- [ ] **Step 4 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_scoring.py -q` → PASS
- [ ] **Step 5 : commit** — `git add -A && git commit -m "feat: ranking score with explanation"`

---

### Task 8 : digest (rendu), emailer et étape notify

**Files :**
- Create : `src/stage_radar/digest.py`, `src/stage_radar/emailer.py`, `src/stage_radar/templates/digest.html.j2`, `src/stage_radar/templates/digest.txt.j2`, `src/stage_radar/stages/notify.py`
- Test : `tests/test_digest.py`, `tests/test_stage_notify.py`

**Interfaces :**
- Consumes : `scoring.*`, `visa.apply_deadline`, `Rules`, `db.fetch_scorable/fetch_unnotified/update_offer/fetch_sources/mark_notified/rejected_sample`, `RunReport`.
- Produces : `digest.DigestItem`, `digest.Digest`, `flag_emoji(code)`, `french_date(d)`, `stats_lines(report)`, `render(digest) -> tuple[subject, html, text]` ; `emailer.Sender` (Protocol `send(subject, html, text)`) ; `ResendSender(api_key, to, from_addr=..., client=None)` ; `FileSender(directory)` ; `stages.notify.NotifyContext(countries, city_costs, rules, scoring_cfg)` ; `run_notify(conn, sender, ctx, today, run_started, report)`.

- [ ] **Step 1 : templates**

`src/stage_radar/templates/digest.txt.j2` :
```jinja
Stages DS — {{ d.date_label }} · {{ d.new_count }} nouvelle(s) offre(s)
{% if d.errors %}
⚠️ ERREURS
{% for where, msg in d.errors.items() %}  - {{ where }} : {{ msg }}
{% endfor %}{% endif %}
📊 {% for line in d.stats %}{{ line }}
   {% endfor %}
{% if d.closing %}⏰ FENÊTRES QUI SE FERMENT
{% for c in d.closing %}  {{ c.flag }} {{ c.name }} — postuler avant le {{ c.deadline }} (dans {{ c.days }} j)
{% endfor %}{% endif %}
{% for it in d.items %}
{{ it.rank }}. {{ it.title }} — {{ it.company }} · {{ it.flag }} {{ it.place }}   [{{ it.score }}]
   {{ it.meta }}
   {% if it.summary %}{{ it.summary }}{% else %}{{ it.snippet }}{% endif %}
   {% if it.requirements %}{{ it.requirements|join(' · ') }}{% endif %}
   {% if it.why %}Pourquoi : {{ it.why }}{% endif %}
   {% for l in it.links %}→ {{ l.source }} : {{ l.url }}
   {% endfor %}
{% endfor %}
{% if d.extra_count %}+ {{ d.extra_count }} autres offres dans Supabase (vue v_inbox).
{% endif %}{% if not d.items %}Aucune nouvelle offre aujourd'hui — le pipeline tourne bien.
{% endif %}
{% if d.audit %}🔍 Audit : rejets pris au hasard
{% for a in d.audit %}  ✗ {{ a.title }} — {{ a.company }} · {{ a.reason }}
{% endfor %}{% endif %}
```

`src/stage_radar/templates/digest.html.j2` :
```jinja
<!doctype html>
<html lang="fr"><body style="margin:0;background:#f6f6f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1d1d1b">
<div style="max-width:680px;margin:0 auto;padding:24px 16px">
  <h1 style="font-size:20px;margin:0 0 4px">📬 Stages DS — {{ d.date_label }}</h1>
  <p style="margin:0 0 16px;color:#5f5f5a">{{ d.new_count }} nouvelle(s) offre(s)</p>
  {% if d.errors %}
  <div style="background:#fdecea;border-radius:8px;padding:12px;margin-bottom:16px">
    <strong>⚠️ Erreurs</strong>
    <ul style="margin:6px 0 0;padding-left:18px">{% for where, msg in d.errors.items() %}<li><b>{{ where }}</b> : {{ msg }}</li>{% endfor %}</ul>
  </div>
  {% endif %}
  <div style="background:#fff;border-radius:8px;padding:12px;margin-bottom:16px;font-size:14px">
    {% for line in d.stats %}<div>📊 {{ line }}</div>{% endfor %}
  </div>
  {% if d.closing %}
  <div style="background:#fff7e6;border-radius:8px;padding:12px;margin-bottom:16px;font-size:14px">
    <strong>⏰ Fenêtres qui se ferment</strong>
    {% for c in d.closing %}<div>{{ c.flag }} {{ c.name }} — postuler avant le {{ c.deadline }} (dans {{ c.days }} j)</div>{% endfor %}
  </div>
  {% endif %}
  {% for it in d.items %}
  <div style="background:#fff;border-radius:8px;padding:14px;margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;gap:8px">
      <div style="font-weight:600">{{ it.rank }}. {{ it.title }}</div>
      <div style="font-weight:700;color:#2b6cb0">{{ it.score }}</div>
    </div>
    <div style="color:#5f5f5a;font-size:14px">{{ it.company }} · {{ it.flag }} {{ it.place }}</div>
    <div style="font-size:13px;margin-top:6px">{{ it.meta }}</div>
    <p style="font-size:14px;margin:8px 0">{% if it.summary %}{{ it.summary }}{% else %}<i>{{ it.snippet }}</i>{% endif %}</p>
    {% if it.requirements %}<div style="font-size:13px;color:#5f5f5a">{{ it.requirements|join(' · ') }}</div>{% endif %}
    {% if it.why %}<div style="font-size:13px;margin-top:4px">Pourquoi : {{ it.why }}</div>{% endif %}
    <div style="font-size:13px;margin-top:8px">{% for l in it.links %}<a href="{{ l.url }}" style="color:#2b6cb0;margin-right:12px">→ {{ l.source }}</a>{% endfor %}</div>
  </div>
  {% endfor %}
  {% if d.extra_count %}<p style="font-size:14px">+ {{ d.extra_count }} autres offres dans Supabase (vue <code>v_inbox</code>).</p>{% endif %}
  {% if not d.items %}<p>Aucune nouvelle offre aujourd'hui — le pipeline tourne bien.</p>{% endif %}
  {% if d.audit %}
  <div style="font-size:13px;color:#5f5f5a;margin-top:20px">
    <strong>🔍 Audit : rejets pris au hasard</strong>
    {% for a in d.audit %}<div>✗ {{ a.title }} — {{ a.company }} · {{ a.reason }}</div>{% endfor %}
  </div>
  {% endif %}
</div></body></html>
```

- [ ] **Step 2 : tests qui échouent**

`tests/test_digest.py` :
```python
from datetime import date

from stage_radar.digest import Digest, DigestItem, flag_emoji, french_date, render, stats_lines
from stage_radar.models import RunReport


def test_helpers():
    assert flag_emoji("NL") == "🇳🇱"
    assert flag_emoji(None) == "🏳️"
    assert french_date(date(2026, 10, 14)) == "mercredi 14 octobre"


def test_stats_lines():
    report = RunReport()
    report.count("collect", "adzuna_seen", 200)
    report.count("collect", "adzuna_new", 150)
    report.count("prefilter", "passed", 20)
    report.count("prefilter", "title_data", 100)
    report.count("classify", "passed", 12)
    report.count("classify", "rejected_work_mode", 8)
    lines = stats_lines(report)
    assert lines[0] == "200 offres vues (150 nouvelles) → 20 après règles → 12 retenues"
    assert "titre sans terme data 100" in lines[1]
    assert "mode de travail 8" in lines[2]


def test_render_contains_items_and_escapes_html():
    item = DigestItem(rank=1, title="ML Intern <script>", company="Adyen", flag="🇳🇱",
                      place="Amsterdam", score=92.0, meta="🟢 +350 €/mois", summary="RAG.",
                      snippet="", requirements=["Python"], why="profil 5/5",
                      links=[{"source": "adzuna", "url": "https://x"}])
    d = Digest(date_label="mercredi 14 octobre", new_count=1, items=[item], extra_count=0,
               stats=["s"], closing=[], audit=[], errors={"adzuna": "HTTP 401"})
    subject, html, text = render(d)
    assert subject == "📬 Stages DS — mercredi 14 octobre · 1 nouvelle(s) offre(s)"
    assert "&lt;script&gt;" in html and "Adyen" in html and "HTTP 401" in html
    assert "ML Intern <script>" in text and "→ adzuna : https://x" in text


def test_render_empty_day():
    d = Digest("jeudi 15 octobre", 0, [], 0, [], [], [], {})
    _, html, text = render(d)
    assert "Aucune nouvelle offre" in html and "Aucune nouvelle offre" in text
```

`tests/test_stage_notify.py` :
```python
from datetime import UTC, date, datetime

from stage_radar import db
from stage_radar.config import load_city_costs, load_countries, load_yaml
from stage_radar.models import OfferStatus, RawOffer, RunReport
from stage_radar.normalize import dedup_key
from stage_radar.rules import Rules
from stage_radar.stages.notify import NotifyContext, run_notify

CTX = NotifyContext(countries=load_countries(), city_costs=load_city_costs(),
                    rules=Rules.from_config(load_yaml("rules.yaml")),
                    scoring_cfg=load_yaml("scoring.yaml"))
TODAY = date(2026, 10, 1)


class CaptureSender:
    def __init__(self):
        self.sent = []

    def send(self, subject, html, text):
        self.sent.append((subject, html, text))


def add(conn, i, status, fit="4", country="NL", city="Amsterdam"):
    raw = RawOffer("adzuna", str(i), f"https://x/{i}", f"Data Intern {i}", f"C{i}", city,
                   country, city, "desc", False, datetime(2026, 9, 30, tzinfo=UTC))
    offer_id, _ = db.upsert_offer(conn, raw, dedup_key(raw.company, raw.title, raw.country))
    db.update_offer(conn, offer_id, status=status,
                    decisions={"profile_fit": {"answer": fit, "p": 0.9}},
                    extracted={"salary_eur_month": 2000}, summary=f"Résumé {i}")
    conn.commit()
    return offer_id


def test_run_notify_scores_sends_and_marks(conn):
    started = conn.execute("select now() - interval '1 minute' as t").fetchone()["t"]
    low = add(conn, 1, OfferStatus.ENRICHED, fit="2")
    high = add(conn, 2, OfferStatus.CLASSIFIED, fit="5")
    rejected = add(conn, 3, OfferStatus.PREFILTERED)
    db.reject(conn, rejected, "classify", "mode de travail = remote (p=0.95)")
    conn.commit()
    sender, report = CaptureSender(), RunReport()
    run_notify(conn, sender, CTX, TODAY, started, report)

    [(subject, html, text)] = sender.sent
    assert "2 nouvelle(s) offre(s)" in subject
    assert text.index("Data Intern 2") < text.index("Data Intern 1")  # tri par score
    assert "🟢 +300 €/mois" in text  # 2000 - 1700 (Amsterdam)
    assert "mode de travail = remote" in text  # audit
    assert db.fetch_unnotified(conn) == []
    scored = {o["id"]: o for o in db.fetch_scorable(conn)}
    assert scored[high]["score"] > scored[low]["score"]
    assert scored[high]["score_breakdown"]["fit"] == 1.0
    assert report.counts["notify"] == {"sent": 2}


def test_run_notify_sends_on_empty_day(conn):
    sender = CaptureSender()
    run_notify(conn, sender, CTX, TODAY, datetime(2026, 10, 1, tzinfo=UTC), RunReport())
    assert "0 nouvelle(s) offre(s)" in sender.sent[0][0]
```

- [ ] **Step 3 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_digest.py tests/test_stage_notify.py -q` → FAIL

- [ ] **Step 4 : implémenter**

`src/stage_radar/digest.py` :
```python
"""Modèle et rendu (HTML + texte) du digest quotidien."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from jinja2 import Environment, PackageLoader, select_autoescape

from stage_radar.models import RunReport

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
PREFILTER_LABELS = {"country": "pays", "title_data": "titre sans terme data",
                    "title_internship": "titre sans terme stage",
                    "title_blacklist": "titre exclu", "too_old": "trop ancienne",
                    "visa_window": "fenêtre visa fermée"}
CLASSIFY_LABELS = {"is_internship_convention": "pas un stage conventionné",
                   "local_enrollment_required": "inscription locale",
                   "duration": "durée", "start": "démarrage",
                   "work_mode": "mode de travail", "other_language_required": "langue"}


@dataclass
class DigestItem:
    rank: int
    title: str
    company: str
    flag: str
    place: str
    score: float
    meta: str
    summary: str
    snippet: str
    requirements: list[str]
    why: str
    links: list[dict]


@dataclass
class Digest:
    date_label: str
    new_count: int
    items: list[DigestItem]
    extra_count: int
    stats: list[str]
    closing: list[dict]
    audit: list[dict]
    errors: dict[str, str] = field(default_factory=dict)


def flag_emoji(code: str | None) -> str:
    if not code or len(code) != 2 or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def french_date(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def stats_lines(report: RunReport) -> list[str]:
    collect = report.counts.get("collect", {})
    seen = sum(v for k, v in collect.items() if k.endswith("_seen"))
    new = sum(v for k, v in collect.items() if k.endswith("_new"))
    pre = report.counts.get("prefilter", {})
    cls = report.counts.get("classify", {})
    lines = [f"{seen} offres vues ({new} nouvelles) → {pre.get('passed', 0)} après règles → "
             f"{cls.get('passed', 0)} retenues"]
    pre_rej = [f"{PREFILTER_LABELS.get(k, k)} {v}" for k, v in sorted(pre.items())
               if k != "passed"]
    if pre_rej:
        lines.append("rejets règles : " + " · ".join(pre_rej))
    cls_rej = [f"{CLASSIFY_LABELS.get(k.removeprefix('rejected_'), k)} {v}"
               for k, v in sorted(cls.items()) if k.startswith("rejected_")]
    if cls_rej:
        lines.append("rejets modèle : " + " · ".join(cls_rej))
    return lines


_env = Environment(loader=PackageLoader("stage_radar", "templates"),
                   autoescape=select_autoescape(["html", "j2"]), trim_blocks=True,
                   lstrip_blocks=True)
_text_env = Environment(loader=PackageLoader("stage_radar", "templates"), autoescape=False,
                        trim_blocks=True, lstrip_blocks=True)


def render(digest: Digest) -> tuple[str, str, str]:
    subject = f"📬 Stages DS — {digest.date_label} · {digest.new_count} nouvelle(s) offre(s)"
    html = _env.get_template("digest.html.j2").render(d=digest)
    text = _text_env.get_template("digest.txt.j2").render(d=digest)
    return subject, html, text
```

`src/stage_radar/emailer.py` :
```python
"""Envoi du digest : Resend (production) ou fichier (dry-run)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

RESEND_URL = "https://api.resend.com/emails"


class Sender(Protocol):
    def send(self, subject: str, html: str, text: str) -> None: ...


class ResendSender:
    def __init__(self, api_key: str, to: str,
                 from_addr: str = "Stage Radar <onboarding@resend.dev>",
                 client: httpx.Client | None = None) -> None:
        self.api_key, self.to, self.from_addr = api_key, to, from_addr
        self.client = client or httpx.Client(timeout=30)

    def send(self, subject: str, html: str, text: str) -> None:
        response = self.client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"from": self.from_addr, "to": [self.to], "subject": subject,
                  "html": html, "text": text},
        )
        response.raise_for_status()


class FileSender:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def send(self, subject: str, html: str, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "digest.html").write_text(html, encoding="utf-8")
        (self.directory / "digest.txt").write_text(f"{subject}\n\n{text}", encoding="utf-8")
```

`src/stage_radar/stages/notify.py` :
```python
"""Étape notify : score, construit et envoie le digest, marque les offres notifiées."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg

from stage_radar import db
from stage_radar.config import Country
from stage_radar.digest import Digest, DigestItem, flag_emoji, french_date, render, stats_lines
from stage_radar.emailer import Sender
from stage_radar.models import RunReport
from stage_radar.rules import Rules
from stage_radar.scoring import ScoreInput, compute, cost_of_living, explain, finance_badge
from stage_radar.visa import apply_deadline

BADGE_ICON = {"profit": "🟢", "even": "🟡", "deficit": "🔴", "unknown": "⚪"}
LABELS = {"onsite": "sur site", "hybrid": "hybride", "remote": "full remote",
          "6plus": "6 mois +", "dec_mar": "déc.-mars"}
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0}


@dataclass(frozen=True)
class NotifyContext:
    countries: dict[str, Country]
    city_costs: dict[str, int]
    rules: Rules
    scoring_cfg: dict


def _deadline(ctx: NotifyContext, country: str | None) -> date | None:
    c = ctx.countries.get((country or "").upper())
    if c is None:
        return None
    return apply_deadline(c, ctx.rules.latest_start, ctx.rules.recruitment_weeks)


def _score(offer: dict, ctx: NotifyContext, today: date) -> tuple[ScoreInput, float, dict, int | None]:
    fit_answer = (offer["decisions"].get("profile_fit") or {}).get("answer")
    salary = offer["extracted"].get("salary_eur_month")
    cost = cost_of_living(offer["city"], offer["country"], ctx.countries, ctx.city_costs)
    inp = ScoreInput(
        profile_fit=int(fit_answer) if fit_answer and str(fit_answer).isdigit() else None,
        posted_at=offer["posted_at"],
        deadline=_deadline(ctx, offer["country"]),
        badge=finance_badge(salary, cost),
        n_flags=len(offer["flags"]),
    )
    score, parts = compute(inp, ctx.scoring_cfg, today)
    balance = round(salary - cost) if salary is not None and cost is not None else None
    return inp, score, parts, balance


def _meta(offer: dict, inp: ScoreInput, balance: int | None) -> str:
    parts = [BADGE_ICON[inp.badge] + (f" {balance:+d} €/mois" if balance is not None
                                      else " salaire inconnu")]
    for key in ("work_mode", "duration", "start"):
        answer = (offer["decisions"].get(key) or {}).get("answer")
        if answer in LABELS:
            parts.append(LABELS[answer])
    parts += [f"⚠️ {f}" for f in offer["flags"]]
    return " · ".join(parts)


def run_notify(conn: psycopg.Connection, sender: Sender, ctx: NotifyContext, today: date,
               run_started: datetime, report: RunReport) -> None:
    for offer in db.fetch_scorable(conn):
        _, score, parts, _ = _score(offer, ctx, today)
        db.update_offer(conn, offer["id"], score=score, score_breakdown=parts)
    conn.commit()

    pending = db.fetch_unnotified(conn)
    ranked = sorted(pending, key=lambda o: o["score"] or 0, reverse=True)
    top_n = ctx.scoring_cfg.get("top_n", 15)
    sources = db.fetch_sources(conn, [o["id"] for o in ranked[:top_n]])
    items = []
    for rank, offer in enumerate(ranked[:top_n], start=1):
        inp, score, parts, balance = _score(offer, ctx, today)
        links = sorted(sources.get(offer["id"], []),
                       key=lambda s: SOURCE_PRIORITY.get(s["source"], 1))
        items.append(DigestItem(
            rank=rank, title=offer["title"], company=offer["company"] or "?",
            flag=flag_emoji(offer["country"]),
            place=offer["city"] or offer["country"] or "",
            score=score, meta=_meta(offer, inp, balance), summary=offer["summary"] or "",
            snippet=(offer["description"] or "")[:280],
            requirements=offer["extracted"].get("key_requirements") or [],
            why=explain(inp, parts, offer["country"], today), links=links,
        ))

    window = ctx.scoring_cfg.get("closing_window_days", 30)
    closing = []
    for c in sorted(ctx.countries.values(), key=lambda c: c.code):
        deadline = _deadline(ctx, c.code)
        if c.in_scope and deadline and today <= deadline <= today + timedelta(days=window):
            closing.append({"flag": flag_emoji(c.code), "name": c.name,
                            "deadline": deadline.strftime("%d/%m"),
                            "days": (deadline - today).days})

    audit = [{"title": r["title"], "company": r["company"] or "?",
              "reason": r["rejected_reason"]}
             for r in db.rejected_sample(conn, run_started, 5)]
    digest = Digest(date_label=french_date(today), new_count=len(ranked), items=items,
                    extra_count=max(0, len(ranked) - top_n), stats=stats_lines(report),
                    closing=closing, audit=audit, errors=dict(report.errors))
    sender.send(*render(digest))
    db.mark_notified(conn, [o["id"] for o in ranked])
    conn.commit()
    report.count("notify", "sent", len(ranked))
```

Note : dans le test, `2000 − 1700` (Amsterdam) = `+300` → badge `profit` (> +200), affiché `🟢 +300 €/mois`.

- [ ] **Step 5 : lancer les tests** — `.venv\Scripts\python -m pytest tests/test_digest.py tests/test_stage_notify.py -q` → PASS
- [ ] **Step 6 : commit** — `git add -A && git commit -m "feat: digest rendering, Resend sender and notify stage"`

---

### Task 9 : orchestration du pipeline et CLI

**Files :**
- Create : `src/stage_radar/pipeline.py`, `src/stage_radar/__main__.py`
- Test : `tests/test_pipeline.py`

**Interfaces :**
- Consumes : toutes les étapes, `build_collectors`, `GeminiClient/GeminiEngine/FakeEngine`, `ResendSender/FileSender`, `fetch_rates`.
- Produces : `pipeline.Components(collectors, engine, llm, sender, rates_loader)` ; `pipeline.Settings` (configs chargées) ; `load_settings() -> Settings` ; `build_components(settings, env, dry_run_dir=None) -> Components` ; `run_pipeline(conn, components, settings, stages, today) -> int` (code de sortie) ; `STAGES`. CLI : `python -m stage_radar migrate | run [--dry-run DIR] [--stages a,b]`.

- [ ] **Step 1 : test qui échoue** — `tests/test_pipeline.py` :
```python
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


def test_end_to_end(conn):
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


def test_all_sources_failing_returns_error_code(conn):
    comps = components([BrokenCollector()])
    assert run_pipeline(conn, comps, load_settings(), STAGES, TODAY) == 1
    assert "down" in comps.sender.sent[0]  # le digest part quand même avec l'erreur
```

- [ ] **Step 2 : lancer et constater l'échec** — `.venv\Scripts\python -m pytest tests/test_pipeline.py -q` → FAIL

- [ ] **Step 3 : implémenter**

`src/stage_radar/pipeline.py` :
```python
"""Assemble les étapes : collect → prefilter → classify → enrich → notify."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml

from stage_radar import db
from stage_radar.collectors import build_collectors
from stage_radar.collectors.base import Collector, redact
from stage_radar.config import Country, load_city_costs, load_countries, load_yaml
from stage_radar.emailer import FileSender, ResendSender, Sender
from stage_radar.engines.base import DecisionEngine, Question, engine_version, load_questions
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import GeminiClient, GeminiEngine
from stage_radar.models import RunReport
from stage_radar.rules import Rules
from stage_radar.salary import fetch_rates
from stage_radar.stages.classify import run_classify
from stage_radar.stages.collect import run_collect
from stage_radar.stages.enrich import JsonClient, run_enrich
from stage_radar.stages.notify import NotifyContext, run_notify
from stage_radar.stages.prefilter import run_prefilter

STAGES = ["collect", "prefilter", "classify", "enrich", "notify"]


@dataclass
class Settings:
    search: dict
    decisions: dict
    scoring: dict
    rules: Rules
    questions: list[Question]
    profile: str
    countries: dict[str, Country]
    city_costs: dict[str, int]


def load_settings() -> Settings:
    decisions = load_yaml("decisions.yaml")
    return Settings(
        search=load_yaml("search.yaml"),
        decisions=decisions,
        scoring=load_yaml("scoring.yaml"),
        rules=Rules.from_config(load_yaml("rules.yaml")),
        questions=load_questions(decisions),
        profile=yaml.safe_dump(load_yaml("profile.yaml"), allow_unicode=True, sort_keys=False),
        countries=load_countries(),
        city_costs=load_city_costs(),
    )


@dataclass
class Components:
    collectors: list[Collector]
    engine: DecisionEngine | None
    llm: JsonClient | None
    sender: Sender
    rates_loader: Callable[[], dict[str, float]]


def build_components(settings: Settings, env: Mapping[str, str],
                     dry_run_dir: Path | None = None) -> Components:
    llm = None
    if env.get("GEMINI_API_KEY"):
        llm = GeminiClient(env["GEMINI_API_KEY"], settings.decisions.get("gemini_model",
                                                                         "gemini-2.5-flash"),
                           min_interval_s=float(settings.decisions.get("gemini_min_interval_s",
                                                                       0)))
    engine: DecisionEngine | None
    if settings.decisions.get("engine") == "fake":
        engine = FakeEngine()
    else:
        engine = GeminiEngine(llm) if llm else None
    if dry_run_dir is not None:
        sender: Sender = FileSender(dry_run_dir)
    else:
        sender = ResendSender(env.get("RESEND_API_KEY", ""), env.get("DIGEST_TO", ""))
    return Components(collectors=build_collectors(settings.search, env), engine=engine,
                      llm=llm, sender=sender, rates_loader=fetch_rates)


def _since(conn: psycopg.Connection, settings: Settings, today: date) -> date:
    last = db.last_run_start(conn)
    if last is None:
        return today - timedelta(days=settings.search.get("lookback_days_first_run", 30))
    return last.date() - timedelta(days=settings.search.get("overlap_days", 2))


def run_pipeline(conn: psycopg.Connection, comps: Components, settings: Settings,
                 stages: list[str], today: date) -> int:
    report = RunReport()
    since = _since(conn, settings, today)
    run_id, started = db.start_run(conn)
    crashed = False
    version = engine_version(comps.engine.name if comps.engine else "none", settings.decisions)
    thresholds = (settings.decisions.get("reject_threshold", 0.85),
                  settings.decisions.get("uncertain_below", 0.6))

    steps: dict[str, Callable[[], None]] = {
        "collect": lambda: run_collect(conn, comps.collectors, since, report),
        "prefilter": lambda: run_prefilter(conn, settings.rules, settings.countries, today,
                                           report),
        "classify": lambda: (
            run_classify(conn, comps.engine, settings.questions, settings.profile, thresholds,
                         version, report)
            if comps.engine else report.error("classify", "GEMINI_API_KEY manquant")
        ),
        "enrich": lambda: (
            run_enrich(conn, comps.llm, comps.rates_loader(), report)
            if comps.llm else report.error("enrich", "GEMINI_API_KEY manquant")
        ),
        "notify": lambda: run_notify(
            conn, comps.sender,
            NotifyContext(settings.countries, settings.city_costs, settings.rules,
                          settings.scoring),
            today, started, report),
    }
    for name in stages:
        try:
            steps[name]()
        except Exception as exc:
            conn.rollback()
            crashed = True
            report.error(name, redact(f"{type(exc).__name__}: {exc}"))

    db.finish_run(conn, run_id, report)
    conn.commit()
    names = [c.name for c in comps.collectors]
    all_sources_failed = "collect" in stages and names and all(n in report.errors for n in names)
    return 1 if crashed or all_sources_failed else 0
```

`src/stage_radar/__main__.py` :
```python
"""CLI : python -m stage_radar migrate | run [--dry-run DIR] [--stages a,b]."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from stage_radar import db
from stage_radar.pipeline import STAGES, build_components, load_settings, run_pipeline


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="stage_radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="applique les migrations SQL")
    run = sub.add_parser("run", help="exécute le pipeline")
    run.add_argument("--stages", default=",".join(STAGES))
    run.add_argument("--dry-run", metavar="DIR", help="écrit le digest dans DIR au lieu de l'envoyer")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL manquant", file=sys.stderr)
        return 2
    with db.connect(url) as conn:
        if args.command == "migrate":
            applied = db.apply_migrations(conn)
            print(f"migrations appliquées : {applied or 'aucune'}")
            return 0
        stages = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = set(stages) - set(STAGES)
        if unknown:
            print(f"étapes inconnues : {sorted(unknown)}", file=sys.stderr)
            return 2
        settings = load_settings()
        dry = Path(args.dry_run) if args.dry_run else None
        comps = build_components(settings, os.environ, dry)
        return run_pipeline(conn, comps, settings, stages, date.today())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4 : lancer toute la suite** — `.venv\Scripts\python -m pytest -q` → PASS ; `.venv\Scripts\python -m ruff check .` → `All checks passed!`
- [ ] **Step 5 : fumée locale en dry-run** (Postgres local via pgserver, `engine: fake` non requis car sans clé Gemini, classify et enrich sont signalés en erreur) :
```bash
.venv\Scripts\python -c "import pgserver; s=pgserver.get_server('out/pg'); print(s.get_uri())"
set DATABASE_URL=<uri affichée>
.venv\Scripts\python -m stage_radar migrate
.venv\Scripts\python -m stage_radar run --dry-run out/digest --stages collect,prefilter,notify
```
Expected : `out/digest/digest.html` généré. La collecte Arbeitsagentur fonctionne sans clé, Adzuna est signalé « clés manquantes ».
- [ ] **Step 6 : commit** — `git add -A && git commit -m "feat: pipeline orchestration and CLI"`

---

### Task 10 : GitHub Actions, README, mise à jour de la spec

**Files :**
- Create : `.github/workflows/ci.yml`, `.github/workflows/daily.yml`, `README.md`
- Modify : `docs/superpowers/specs/2026-09-23-stage-radar-design.md` (écarts listés en tête de ce plan)

- [ ] **Step 1 : `.github/workflows/ci.yml`**
```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: pytest -q
```

- [ ] **Step 2 : `.github/workflows/daily.yml`**
```yaml
name: daily
on:
  schedule:
    - cron: "0 5 * * *"   # ~07:00 Paris (été) / 06:00 (hiver)
  workflow_dispatch:
concurrency:
  group: daily
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      ADZUNA_APP_ID: ${{ secrets.ADZUNA_APP_ID }}
      ADZUNA_APP_KEY: ${{ secrets.ADZUNA_APP_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
      DIGEST_TO: ${{ secrets.DIGEST_TO }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e .
      - run: python -m stage_radar migrate
      - run: python -m stage_radar run
```

- [ ] **Step 3 : README.md** : présentation, architecture (schéma de la spec), mise en place pas à pas (projet Supabase et URL du pooler, clés Adzuna, Gemini et Resend, GitHub Secrets, premier `workflow_dispatch`), usage local (`.env`, `run --dry-run`), vues Supabase Studio, réglage (`config/*.yaml`), roadmap v1.1/v2.

- [ ] **Step 4 : mettre à jour la spec** (§3 tables countries/city_costs → CSV ; colonne flags ; §5.1 liste noire ; §9 évaluation → v1.1 ; §11 secrets → `DATABASE_URL` ; commandes `python -m stage_radar`).

- [ ] **Step 5 : vérification finale** — `.venv\Scripts\python -m pytest -q` et `.venv\Scripts\python -m ruff check .` → tout vert.

- [ ] **Step 6 : commit** — `git add -A && git commit -m "ci: GitHub Actions workflows, README and spec update"`
