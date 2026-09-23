# Stage Radar

Chaque matin, un email avec les offres de **stage Data Science de 6 mois à l'étranger** auxquelles je peux
réellement postuler : collectées, dédoublonnées, filtrées, enrichies et classées. Le pipeline tourne
gratuitement et sans serveur sur GitHub Actions.

```
GitHub Actions (cron quotidien)
  ├─ collect     Adzuna (~13 pays) · Bundesagentur für Arbeit (DE)
  ├─ prefilter   règles déterministes : pays, titre, fraîcheur, fenêtre visa
  ├─ classify    questions typées (Gemini, puis Laya en local) + seuils de probabilité
  ├─ enrich      salaire, dates, résumé (Gemini, sortie JSON contrainte par schéma)
  └─ notify      score 0-100, digest email (Resend)
          │
          ▼
   Supabase Postgres : machine à états
   collected → prefiltered → classified → enriched → notified | rejected
```

Principes :
- **Une bonne offre ratée coûte plus cher qu'une offre inutile lue.** Le modèle ne rejette qu'au-delà de p ≥ 0,85. Sous ce seuil, l'offre passe avec un badge ⚠️ qui dit quoi vérifier.
- **Chaque rejet est traçable** (étape et raison). Le digest contient un échantillon aléatoire de rejets pour auditer les filtres.
- **Reprise automatique** : un quota épuisé ou une source en panne ne perd rien, le travail restant est repris au passage suivant.

Conception détaillée : [`docs/superpowers/specs/2026-09-23-stage-radar-design.md`](docs/superpowers/specs/2026-09-23-stage-radar-design.md).

## Mise en place (environ 20 minutes, 0 €)

1. **Supabase** : créer un projet gratuit, puis *Project Settings → Database → Connection string → Session pooler*. Copier l'URI : c'est `DATABASE_URL`. Le pooler est nécessaire, car GitHub Actions n'a pas d'IPv6.
2. **Adzuna** : créer un compte sur <https://developer.adzuna.com> pour obtenir `ADZUNA_APP_ID` et `ADZUNA_APP_KEY`.
3. **Gemini** : générer une clé sur <https://aistudio.google.com/apikey> (`GEMINI_API_KEY`, palier gratuit).
4. **Resend** : créer un compte sur <https://resend.com> et une clé API (`RESEND_API_KEY`). Sans domaine vérifié, les emails partent de `onboarding@resend.dev` et ne peuvent aller **qu'à l'adresse du compte Resend**, donc `DIGEST_TO` doit être cette adresse.
5. **GitHub** : *Settings → Secrets and variables → Actions*, ajouter `DATABASE_URL`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `GEMINI_API_KEY`, `RESEND_API_KEY`, `DIGEST_TO`.
6. Onglet *Actions → daily → Run workflow* pour le premier passage, qui récupère 30 jours d'offres. Ensuite le workflow tourne chaque jour vers 7 h (heure de Paris).

## En local

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env        # puis renseigner les valeurs
.venv\Scripts\python -m stage_radar migrate
.venv\Scripts\python -m stage_radar run --dry-run out/digest   # écrit le digest au lieu de l'envoyer
.venv\Scripts\python -m stage_radar run --stages collect,prefilter
```

Tests : `.venv\Scripts\python -m pytest`. Les tests d'intégration démarrent un vrai Postgres local via
`pgserver`, sans Docker. On peut aussi pointer `TEST_DATABASE_URL` vers une base jetable.

## Suivre ses candidatures (v1 : Supabase Studio)

| Vue | Contenu |
|---|---|
| `v_inbox` | offres retenues à traiter, triées par score |
| `v_tracking` | candidatures en cours (`applied`, `interview`, `offer`) |
| `v_rejected_recent` | rejets des 7 derniers jours avec leur raison |

Pour faire avancer une candidature, modifier `applications.user_status` dans le Table Editor
(`new → interested → applied → interview → offer / rejected / dismissed`).

## Réglages

| Fichier | Rôle |
|---|---|
| `config/search.yaml` | pays, mots-clés et budget d'appels par source |
| `config/rules.yaml` | dernier démarrage accepté, listes de mots du titre, âge maximal |
| `config/decisions.yaml` | moteur, questions typées, seuils de rejet et d'incertitude |
| `config/scoring.yaml` | poids du score, taille du top |
| `config/profile.yaml` | profil (sans coordonnées) utilisé pour l'adéquation |
| `seeds/countries.csv`, `seeds/city_costs.csv` | délais de visa et coût de la vie (estimations à ajuster) |

## Feuille de route

- **v1.1** : moteur Laya (local, CPU, probabilités calibrées) et jeu d'évaluation (taux de faux rejets ≤ 5 %), sources Jooble, JobTech et ATS (Greenhouse, Lever, Ashby) avec découverte automatique.
- **v2** : dashboard Next.js sur Vercel (Supabase Auth), actions depuis l'email, SetFit entraîné sur mes choix.
