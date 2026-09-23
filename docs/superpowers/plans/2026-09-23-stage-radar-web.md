# Stage Radar — Interface locale — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** interface Next.js locale (boîte de réception, suivi, rejets, santé) branchée sur la base Postgres du pipeline.

**Architecture :** App Router ; pages en Server Components qui lisent Postgres via `web/lib/queries.ts` ; mutations en Server Actions (`web/app/actions.ts`) ; logique pure testée (`web/lib/*.ts`) ; composants client uniquement pour le clavier et les formulaires.

**Tech Stack :** Next.js 16.3, React, TypeScript, Tailwind 4, `postgres` 3.4, `yaml`, Vitest 4 ; côté Python : nouvelle étape `score`.

Spec : `docs/superpowers/specs/2026-09-23-stage-radar-web-design.md`.

## Global Constraints

- Accès base côté serveur uniquement (`import "server-only"` dans `lib/db.ts`) ; `prepare: false`.
- `DATABASE_URL` lu depuis `../.env` (racine du dépôt) ; jamais exposé au client.
- Écritures limitées à `applications` (+ statut d'offre pour le repêchage).
- Libellés d'interface en français.

---

### Task 1 : étape `score` (Python)

**Files :** create `src/stage_radar/stages/score.py` ; modify `stages/notify.py`, `pipeline.py` ; test `tests/test_stage_score.py`, adapter `tests/test_stage_notify.py`, `tests/test_pipeline.py`.

**Interfaces :** `run_score(conn, ctx: NotifyContext, today, report)` ; `STAGES = [collect, prefilter, classify, enrich, score, notify]` ; `run_notify` lit `score` en base, sans le recalculer ; la fiche du digest garde son « pourquoi » (recalcul local non persisté).

- [ ] Test : `run_score` remplit `score` et `score_breakdown` des offres classified/enriched/notified et compte `report.counts["score"]["scored"]`.
- [ ] Implémenter en déplaçant la boucle de scoring de notify ; faire appeler `run_score` avant notify dans les tests de notify existants.
- [ ] pytest + ruff verts ; commit.

### Task 2 : scaffold Next.js + accès base

**Files :** `web/` (create-next-app), `web/next.config.ts` (charge `../.env`), `web/lib/db.ts`, `web/vitest.config.ts`.

**Interfaces :** `sql` (client `postgres`, singleton global en dev).

- [ ] `npx create-next-app@16.3.6 web` (TypeScript, Tailwind, App Router, ESLint, npm) ; ajouter `postgres`, `yaml`, `server-only`, `vitest`.
- [ ] `npm run build` passe ; commit.

### Task 3 : logique pure + tests (Vitest)

**Files :** `web/lib/labels.ts`, `web/lib/filters.ts`, `web/lib/keyboard.ts`, `web/lib/visa.ts`, tests `web/lib/*.test.ts`.

**Interfaces :**
- `STATUS_LABELS`, `labelFor(status): boolean | null` (règles de la spec).
- `parseFilters(searchParams) -> {country?, status: "new"|"interested"|"all", q?}` ; `toQuery(filters, patch)`.
- `keyAction(event) -> {type: "next"|"prev"|"status"|"open"|"search", status?}` ; ignoré si la cible est un champ de saisie.
- `visaWindows(countries, latestStart, recruitmentWeeks, today) -> {code, name, deadline, daysLeft}[]`.

- [ ] Écrire les tests, les voir échouer, implémenter, `npx vitest run` vert ; commit.

### Task 4 : requêtes et actions

**Files :** `web/lib/queries.ts`, `web/lib/config.ts` (lecture rules.yaml / countries.csv), `web/app/actions.ts`.

**Interfaces :** `getInbox(filters)`, `getOffer(id)`, `getTracking()`, `getRejected(days, stage?)`, `getHealth()` ; actions `setStatus(offerId, status)`, `saveNotes(offerId, notes)`, `rescue(offerId)` (transaction ; `revalidatePath`).

- [ ] Build vert ; commit.

### Task 5 : pages et composants

**Files :** `web/app/layout.tsx` (navigation), `web/app/page.tsx`, `web/app/suivi/page.tsx`, `web/app/rejets/page.tsx`, `web/app/sante/page.tsx`, `web/components/*`.

- [ ] Boîte de réception (liste + fiche + raccourcis clavier), suivi en colonnes, rejets + repêcher, santé.
- [ ] `npm run build` + `npx vitest run` + lint verts ; commit.

### Task 6 : vérification réelle et docs

- [ ] `.claude/launch.json` pour le serveur de dev ; parcourir les 4 pages dans le navigateur intégré (lecture seule).
- [ ] README (section « Interface locale ») ; `daily.yml` inchangé (score inclus dans `run`) ; commit.
