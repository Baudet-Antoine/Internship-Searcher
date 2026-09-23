# Stage Radar — Document de conception

- **Date :** 2026-09-23
- **Statut :** validé ; v1.0 implémentée (écarts d'implémentation intégrés ci-dessous)
- **Auteur :** Antoine Baudet

## 1. Objectif

Réduire le temps de recherche d'un stage de Data Science à l'étranger en produisant, chaque matin, une liste
dédoublonnée, filtrée et classée des offres auxquelles l'utilisateur peut réellement postuler, ainsi qu'un
suivi des candidatures.

### Profil de recherche (critères éliminatoires)

| Critère | Règle |
|---|---|
| Période | Stage ≥ 6 mois, terminé au plus tard en septembre 2027. Démarrage entre décembre 2026 et le 1er mars 2027 inclus. |
| Contrat | Stage sous convention d'école uniquement. Exclus : CDI/CDD, Werkstudent / working student, graduate program, freelance, stages réservés aux étudiants inscrits localement. |
| Lieu | Hors France. Sur site ou hybride, jamais full remote. |
| Langues | Anglais et français OK, espagnol toléré (B1). Rejet si une autre langue est exigée. |
| Visa | Rejet si la date limite réaliste pour postuler dans le pays est dépassée (voir §5.1). |
| Intitulé | Large : Data Science, ML, IA/LLM, Data Engineering, Analytics, Research. |

### Critères de classement (jamais éliminatoires)

Adéquation au profil, fraîcheur, urgence de la fenêtre visa, rentabilité financière (salaire moins coût de
la vie ; le salaire n'est jamais bloquant) et certitude des décisions.

### Contraintes

- **Coût : 0 €.** Uniquement des paliers gratuits.
- **Délai :** le produit minimal doit être utilisable en quelques jours, car la fenêtre de candidature est d'environ 3 mois.
- **Sans serveur :** exécution par GitHub Actions.
- **Dépôt public, données privées :** le code est public (portfolio). Les offres, candidatures et labels vivent uniquement dans Supabase. Les secrets sont dans les GitHub Secrets et les variables d'environnement Vercel.

## 2. Architecture

```
GitHub Actions — daily.yml (cron ~07:00 Europe/Paris + workflow_dispatch)
  │
  ├─ collect     Adzuna · Jooble · Bundesagentur für Arbeit · JobTech (SE) · ATS (Greenhouse/Lever/Ashby)
  ├─ prefilter   règles déterministes (rules.yaml) + fenêtre visa
  ├─ classify    Laya (local, CPU) — questions typées + probabilités (decisions.yaml)
  ├─ enrich      Gemini (palier gratuit) — extraction structurée + résumé
  └─ notify      scoring (scoring.yaml) puis digest email (Resend)
          │
          ▼
     Supabase Postgres  ◄── v1 : Supabase Studio (vues SQL)
                        ◄── v2 : dashboard Next.js sur Vercel (hors périmètre de cette spec)
```

**Orchestration : la base comme machine à états.** Chaque offre porte un statut, et chaque étape est un module
indépendant qui ne traite que les offres au statut précédent. Conséquences : reprise automatique après une
panne ou un quota épuisé, étapes relançables individuellement (`python -m stage_radar run --stages <étape>`), et traçabilité
de chaque rejet.

```
collected → prefiltered → classified → enriched → notified
      └──────────┴────────────┴──→ rejected (rejected_stage, rejected_reason)
```

**Stack :** pipeline en Python 3.12 ; Supabase (Postgres + Auth + RLS) ; Resend ; GitHub Actions.

## 3. Modèle de données

Principe : l'état du pipeline (`offers`) est séparé de l'état utilisateur (`applications`). Le pipeline
n'écrit jamais `applications.user_status`.

| Table | Colonnes principales |
|---|---|
| `offers` | `id uuid`, `dedup_key text unique`, `title`, `company`, `country` (ISO-2), `city`, `description`, `description_is_full bool`, `posted_at`, `collected_at`, `last_seen_at`, `status` (enum), `rejected_stage`, `rejected_reason`, `decisions jsonb`, `flags jsonb` (points à vérifier), `extracted jsonb`, `score numeric`, `score_breakdown jsonb`, `summary`, `engine_version`, `notified_at` |
| `offer_sources` | `offer_id → offers`, `source`, `source_id`, `url`, `first_seen_at`, `raw jsonb` ; `UNIQUE(source, source_id)` |
| `applications` | `offer_id → offers` (1–1), `user_status` enum (`new`, `interested`, `applied`, `interview`, `offer`, `rejected`, `dismissed`), `notes`, `label bool` (futur entraînement SetFit), `updated_at` |
| `runs` | `id`, `started_at`, `finished_at`, `counts jsonb` (par étape et par raison de rejet), `errors jsonb` (par source) |
| `eval_labels` (v1.1) | `offer_id`, `question`, `expected_answer`, `labeled_at` |

- **`decisions` :** `{"<question>": {"answer": ..., "p": 0.97}, ...}`.
- **`engine_version` :** hash du moteur et de `decisions.yaml`. Il permet de reclasser les offres traitées avec une ancienne configuration.
- **Dédoublonnage :** (1) exact, via `UNIQUE(source, source_id)` avec mise à jour de `last_seen_at` ; (2) entre sources, via `dedup_key = sha1(norm(company) | norm(title) | country)`, où la normalisation passe en minuscules, retire les accents, les suffixes légaux (GmbH, Ltd, B.V., SA…) et les mentions « (m/w/d) », « (f/m/x) » et la ponctuation. Pas de dédoublonnage flou en v1.
- **Référentiels :** `seeds/countries.csv` (`code`, `name`, `visa_lead_weeks`, `cost_of_living_eur`, `in_scope`) et `seeds/city_costs.csv` sont des CSV versionnés lus par le code. Aucune requête SQL n'en a besoin en v1, donc pas de table.
- **Trigger :** à l'entrée d'une offre dans `classified` (offre non rejetée), une ligne `applications` est créée avec le statut `new`.
- **Sécurité :** RLS activée sur toutes les tables, vues en `security_invoker`. Le pipeline se connecte en Postgres direct via `DATABASE_URL` (pooler Supabase, compatible IPv4). La lecture côté client (v2) nécessite une session Supabase Auth dont l'email figure dans une allowlist.

## 4. Collecteurs

```python
class Collector(Protocol):
    name: str
    def fetch(self, since: date) -> Iterator[RawOffer]: ...

@dataclass
class RawOffer:
    source: str; source_id: str; url: str
    title: str; company: str | None
    location_raw: str; country: str | None
    description: str; description_is_full: bool
    posted_at: datetime | None
    raw: dict
```

- Un collecteur ne filtre pas et n'écrit pas en base. L'orchestrateur normalise, calcule `dedup_key` et insère avec `ON CONFLICT`.
- Chaque collecteur est isolé : son échec est consigné dans `runs.errors` et les autres continuent.
- **`search.yaml` :** mots-clés multilingues (EN/DE/ES/NL/FR) croisés avec les pays cibles.
- **Fenêtre de récupération :** 30 jours au premier lancement, puis depuis le dernier passage réussi moins 2 jours.
- **Quotas :** un budget d'appels par source et par exécution est défini dans `search.yaml`.

| Source (v1) | Couverture | Description complète |
|---|---|---|
| Adzuna | ~15 pays hors France | non (extrait, à confirmer) |
| Jooble | Mondial | non (extrait) |
| Bundesagentur für Arbeit | Allemagne, filtre stage natif (`angebotsart=34`). Recherche `/pc/v6/jobs` (v4 renvoie 403), détail `/pc/v4/jobdetails/{base64(ref)}`. La date d'entrée est ajoutée en tête de description. | oui (endpoint de détail) |
| JobTech | Suède | oui |
| ATS : Greenhouse, Lever, Ashby | Entreprises listées dans `companies.yaml` | oui |

- **v1.1 : découverte ATS automatique.** Une URL d'offre pointant vers `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>` ou `jobs.ashbyhq.com/<slug>` ajoute le slug à la liste surveillée (table ou fichier généré).
- **Hors périmètre :** LinkedIn, Indeed, Welcome to the Jungle, scraping des pages d'offres.

## 5. Filtrage et décisions

### 5.1 Étage A : règles (`collected → prefiltered`)

| Règle | Rejet si |
|---|---|
| Pays | `FR`, ou pays hors de `countries.in_scope` |
| Liste blanche du titre | aucun terme data dans le titre, **ou** aucun terme stage ni dans le titre ni dans les 1 500 premiers caractères de la description (`rules.yaml`) |
| Liste noire du titre | senior, head of, director, summer, part time, teilzeit (Werkstudent seul est déjà éliminé par la liste blanche ; « manager/lead » éliminaient des stages valides) |
| Fraîcheur | `posted_at` il y a plus de 45 jours |
| Fenêtre visa | `today > date_limite_postuler(pays)` |

```
dernier_démarrage     = 2027-03-01
date_limite_postuler  = dernier_démarrage − visa_lead_weeks − 4 semaines (recrutement)
```

### 5.2 Étage B : moteur de décision (`prefiltered → classified`)

Interface commune `DecisionEngine.classify(text, questions) -> dict[question, (answer, p)]`. Implémentations :
`LayaEngine` (cible), `GeminiEngine`, `FakeEngine` (tests) ; `OpenDecisionEngine` et `JevEngine` en
option. Le moteur est choisi dans `decisions.yaml`.

`GeminiEngine` ne fournit pas de probabilités calibrées : il renvoie pour chaque question une réponse et un
niveau de confiance déclaré (`high` = 0.9, `medium` = 0.7, `low` = 0.5), converti en `p` pour appliquer les
mêmes zones de décision.

| Question | Type | Réponse éliminatoire |
|---|---|---|
| `is_internship_convention` | oui/non | non |
| `local_enrollment_required` | oui/non | oui |
| `duration` | `<4` · `4-5` · `6+` · `unspecified` | `<4`, `4-5` |
| `start` | `before_dec` · `dec_mar` · `after_mar` · `unspecified` | `before_dec`, `after_mar` |
| `work_mode` | `onsite` · `hybrid` · `remote` · `unspecified` | `remote` |
| `other_language_required` | oui/non | oui |
| `profile_fit` | score 1–5 (entrée : offre + `profile.yaml`) | jamais |

**Zones de décision** (p = probabilité de la réponse éliminatoire) :
- `p ≥ 0.85` → `rejected`, avec la raison (ex. `work_mode=remote (p=0.93)`).
- `0.5 ≤ p < 0.85` ou `unspecified` → l'offre passe avec un flag `⚠️` qui précise quoi vérifier.
- `p < 0.5` → l'offre passe.

**Principe :** un faux rejet coûte plus cher qu'un faux positif. Les seuils sont configurables dans `decisions.yaml`.

`profile.yaml` : version nettoyée du CV (compétences, expériences, projets), sans aucune coordonnée.

## 6. Enrichissement et scoring

### 6.1 Gemini (`classified → enriched`)

Sortie structurée validée par Pydantic :

```python
class Extraction(BaseModel):
    salary_amount: float | None
    salary_currency: str | None
    salary_period: Literal["month", "year", "hour"] | None
    start_date: str | None          # "YYYY-MM"
    duration_months: int | None
    city: str | None
    summary_fr: str                 # 2 lignes : mission + stack
    key_requirements: list[str]     # max 5
```

- La consigne impose `null` si l'information est absente du texte.
- **Plausibilité :** un salaire mensuel en EUR hors de [300, 6000] ou une date hors de la fenêtre est remis à `null` et consigné.
- **Secours par expressions régulières** sur le salaire si Gemini renvoie `null` alors qu'un montant est présent.
- **Conversion EUR** via l'API Frankfurter (taux BCE), une fois par exécution.

### 6.2 Rentabilité financière

`solde = salaire_EUR_mensuel − coût_de_la_vie(ville, à défaut pays)`

🟢 > +200 € · 🟡 ±200 € · 🔴 < −200 € · ⚪ salaire inconnu (non pénalisé)

### 6.3 Score (0–100, classement uniquement)

| Composante | Poids | Calcul |
|---|---|---|
| Adéquation au profil | 45 % | `(profile_fit − 1) / 4` |
| Fraîcheur | 20 % | décroissance linéaire sur 14 jours |
| Urgence | 15 % | 1 si la fenêtre visa se ferme dans 21 jours ou moins, décroissance jusqu'à 0 à 90 jours |
| Finances | 10 % | 🟢 1 · 🟡 0,7 · ⚪ 0,5 · 🔴 0,2 |
| Certitude | 10 % | `1 − 0,25 × nb_flags`, avec un plancher à 0 |

Le score est (re)calculé au début de l'étape `notify` pour toutes les offres `classified` ou `enriched` :
la fraîcheur et l'urgence dépendent de la date du jour, et une offre non enrichie reçoit un score avec la
composante finances à ⚪. Les poids sont définis dans `scoring.yaml`. `score_breakdown` est stocké pour que le digest explique le rang de chaque offre.

## 7. Restitution

### 7.1 Digest email (étape `notify`)

- Il contient les offres au statut `classified` ou `enriched` dont `notified_at` est nul, qui passent ensuite à `notified`. Une offre `classified` non enrichie (quota Gemini épuisé) est incluse avec un extrait brut à la place du résumé.
- **Contenu :** statistiques de la veille (collectées, retenues, rejets par étape et par raison) ; « Fenêtres qui se ferment » (pays dont la date limite tombe dans 30 jours ou moins) ; top 15 par score (titre, entreprise, ville et drapeau, badges finances et ⚠️, mode, durée, résumé, compétences, « Pourquoi », tous les liens sources avec l'ATS en premier) ; « + N autres » ; audit de 5 rejets tirés au hasard avec leur raison ; bandeau d'erreurs des sources en tête si besoin.
- Un email est envoyé **même sans nouvelle offre**, pour confirmer que le pipeline tourne.
- **Technique :** gabarit Jinja2 HTML avec une version texte, envoyé par Resend (offre gratuite, destinataire = email du compte).

### 7.2 v1 : Supabase Studio

| Vue | Contenu |
|---|---|
| `v_inbox` | offres retenues avec `user_status = new` ou `interested`, triées par score |
| `v_tracking` | `user_status` parmi `applied`, `interview`, `offer` |
| `v_rejected_recent` | rejets des 7 derniers jours, avec étape et raison |

L'utilisateur fait avancer ses candidatures en éditant `applications.user_status` dans Studio.

### 7.3 v2 (hors périmètre de cette spec)

Dashboard Next.js sur Vercel (Supabase Auth par lien magique et allowlist) ; actions « intéressé / écarter »
depuis l'email ; entraînement hebdomadaire de SetFit sur `applications.label`. Ces points feront l'objet d'une spec dédiée.

## 8. Gestion des erreurs

| Situation | Comportement |
|---|---|
| HTTP 5xx / 429 d'une source | 3 nouvelles tentatives avec délai exponentiel (`tenacity`), puis abandon de la source |
| HTTP 401 / 403 | Échec immédiat de la source, avec erreur explicite |
| Quota Gemini épuisé | Arrêt propre de l'étape ; les offres restantes sont reprises au passage suivant |
| Échec du chargement de Laya | Étape sautée, offres laissées en `prefiltered`, signalé dans le digest |
| Base injoignable ou toutes les sources en échec | Le workflow échoue (notification GitHub) |
| Échec partiel | Le workflow réussit, avec un bandeau d'erreur dans le digest |

- **Idempotence :** sélection par statut, `ON CONFLICT` et transitions atomiques. `concurrency` GitHub Actions empêche deux exécutions simultanées.
- **Cache :** `actions/cache` sur le cache Hugging Face (poids de Laya) et sur pip.
- **Pause Supabase :** l'exécution quotidienne suffit à éviter la mise en pause du projet gratuit.

## 9. Évaluation (v1.1, avec Laya)

1. La commande `python -m pipeline eval-sample` tire environ 60 offres `prefiltered` diversifiées (pays, sources) dans `eval_labels`. L'utilisateur saisit ensuite les réponses attendues.
2. La commande `python -m pipeline eval --engine <nom>` calcule, question par question, le **taux de faux rejets** (métrique principale), la justesse et la calibration (ECE, table de fiabilité).
3. **Règle d'adoption :** un moteur ou un seuil n'est retenu que si son taux de faux rejets est ≤ 5 % sur le jeu d'évaluation.

## 10. Tests

| Niveau | Portée | Moyen |
|---|---|---|
| Unitaires | normalisation et `dedup_key`, règles, date limite visa, scoring, regex salaire, plausibilité | pytest, fonctions pures |
| Collecteurs | réponse API → `RawOffer` | fixtures JSON enregistrées, sans réseau |
| Moteurs | pipeline complet sans modèle | `FakeEngine` |
| Intégration | transitions de statut, idempotence, trigger `applications` | Postgres réel via `pgserver` (local et CI), ou `TEST_DATABASE_URL` |

`ci.yml` (sur push et PR) exécute `ruff` et `pytest`. `daily.yml` exécute le pipeline.

## 11. Configuration et secrets

- **Fichiers versionnés :** `search.yaml`, `rules.yaml`, `decisions.yaml`, `scoring.yaml`, `companies.yaml`, `profile.yaml`, `seeds/countries.csv`, `seeds/city_costs.csv`.
- **GitHub Secrets :** `DATABASE_URL`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `GEMINI_API_KEY`, `RESEND_API_KEY`, `DIGEST_TO`.

## 12. Points à vérifier au début de l'implémentation

- Adzuna : longueur réelle des descriptions, quotas du palier gratuit, liste des pays disponibles.
- Jooble : quotas et format de réponse.
- Bundesagentur für Arbeit : code du filtre « stage » et accès à l'endpoint de détail.
- Laya : package PyPI, API Python, taille réelle, temps d'inférence sur un runner `ubuntu-latest`.
- Gemini : modèle Flash disponible sur le palier gratuit, quotas, support de `response_schema`.
- Resend : conditions d'envoi sans domaine vérifié.
- Valeurs initiales de `visa_lead_weeks` et de `cost_of_living_eur` par pays.

## 13. Découpage de livraison

| Jalon | Contenu |
|---|---|
| **v1.0** | schéma Supabase et seeds ; collecteurs Adzuna et BA ; règles ; `FakeEngine` et `GeminiEngine` ; scoring ; digest ; `daily.yml` et `ci.yml`. **Objectif : premier digest réel sous environ 3 jours.** |
| **v1.1** | `LayaEngine` ; outillage d'évaluation ; collecteurs Jooble, JobTech et ATS ; découverte ATS automatique. |
| **v2** | dashboard Vercel, SetFit (spec séparée). |

Pour garantir un premier digest rapide, v1.0 utilise Gemini comme moteur de décision via l'interface commune.
Laya le remplace en v1.1, après évaluation.
