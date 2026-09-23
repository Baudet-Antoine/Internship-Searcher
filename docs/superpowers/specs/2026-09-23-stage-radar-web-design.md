# Stage Radar — Interface locale (web) — Conception

- **Date :** 2026-09-23
- **Statut :** validé
- **Complète :** `2026-09-23-stage-radar-design.md` (§7.3 « v2 » : cette interface en est la première étape, locale)

## Objectif

Une interface simple qui connecte l'utilisateur à ses données : trier les offres retenues, suivre les
candidatures, auditer les rejets et vérifier la santé du pipeline. Elle remplace Supabase Studio pour
l'usage quotidien.

## Contraintes

- Exécution **locale uniquement** (`cd web && npm run dev`) : aucune authentification en v1.
- Next.js 16 (App Router, TypeScript, Tailwind). Accès Postgres **côté serveur uniquement**
  (bibliothèque `postgres`, `prepare: false` pour le pooler Supabase), même `DATABASE_URL` que le
  pipeline, lu depuis `../.env`. Aucun secret n'est envoyé au navigateur.
- L'interface n'écrit que `applications` (statut, notes, label) et, pour le repêchage, le statut de
  l'offre. Le pipeline reste seul maître des autres colonnes.
- Déploiement Vercel ultérieur possible : il exigera Supabase Auth ; l'accès aux données est isolé dans
  `web/lib/` pour faciliter cet ajout.

## Pages

| Route | Contenu | Actions |
|---|---|---|
| `/` Boîte de réception | Liste des offres retenues (`user_status` ∈ new, interested) triée par score ; filtres pays / statut / recherche texte dans l'URL ; fiche détaillée de l'offre sélectionnée (`?id=`) : résumé, compétences, salaire et solde, flags, « pourquoi », décisions du modèle avec p, description, liens | Statut, notes. Clavier : `j`/`k` naviguer, `i` intéressé, `a` postulé, `x` écarter, `o` ouvrir le lien principal, `/` recherche |
| `/suivi` | Colonnes Intéressé → Postulé → Entretien → Offre ; Refusé et Écarté repliés | Avancer / reculer d'une colonne, notes |
| `/rejets` | Offres rejetées sur 7 ou 30 jours, filtre par étape ; raison affichée | **Repêcher** : offre → `classified`, flag « repêchée manuellement », `applications` = new, `label = true` |
| `/sante` | Compteurs par statut ; 14 derniers runs (compteurs, erreurs, durée) ; fenêtres visa (date limite par pays) | Lecture seule |

## Règles

- **Label automatique** (`applications.label`) : interested, applied, interview, offer → `true` ;
  dismissed → `false` ; new et rejected (refus de l'employeur) → inchangé. Repêchage → `true`.
- **Repêchage** : si l'offre n'a jamais été classée (rejet par règles), elle passe `classified` sans
  décisions ; le passage au statut `classified` déclenche la création de la ligne `applications`
  (trigger existant), mise ensuite à `new` si elle existait.
- **Fenêtre visa** : `date_limite = latest_start − (visa_lead_weeks + recruitment_weeks) semaines`,
  lue depuis `../config/rules.yaml` et `../seeds/countries.csv` (même formule que `visa.py`).

## Ajout côté pipeline

- Nouvelle étape **`score`** (`python -m stage_radar run --stages score`) : calcule score et
  `score_breakdown` de toutes les offres retenues, sans envoyer d'email. `STAGES` devient
  `collect, prefilter, classify, enrich, score, notify` ; notify ne recalcule plus le score.

## Vérification

- Vitest : mapping clavier → action, parsing des filtres d'URL, fenêtre visa, règles de label.
- pytest : étape `score`.
- Parcours réel des 4 pages dans le navigateur intégré, en lecture seule sur la base de l'utilisateur.
