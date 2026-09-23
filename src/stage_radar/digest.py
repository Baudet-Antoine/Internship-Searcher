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


def one_line(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def render_text(d: Digest) -> str:
    out = [f"Stages DS — {d.date_label} · {d.new_count} nouvelle(s) offre(s)", ""]
    if d.errors:
        out.append("⚠️ ERREURS")
        out += [f"  - {where} : {msg}" for where, msg in d.errors.items()]
        out.append("")
    out += [f"📊 {line}" for line in d.stats]
    if d.closing:
        out += ["", "⏰ FENÊTRES QUI SE FERMENT"]
        out += [f"  {c['flag']} {c['name']} — postuler avant le {c['deadline']} "
                f"(dans {c['days']} j)" for c in d.closing]
    for it in d.items:
        out += ["", f"{it.rank}. {it.title} — {it.company} · {it.flag} {it.place}   "
                    f"[{it.score}]", f"   {it.meta}",
                f"   {it.summary or one_line(it.snippet, 280)}"]
        if it.requirements:
            out.append("   " + " · ".join(it.requirements))
        if it.why:
            out.append(f"   Pourquoi : {it.why}")
        out += [f"   → {link['source']} : {link['url']}" for link in it.links]
    out.append("")
    if d.extra_count:
        out.append(f"+ {d.extra_count} autres offres dans Supabase (vue v_inbox).")
    if not d.items:
        ok = "" if d.errors else " — le pipeline tourne bien"
        out.append(f"Aucune nouvelle offre retenue aujourd'hui{ok}.")
    if d.audit:
        out += ["", "🔍 Audit : rejets pris au hasard"]
        out += [f"  ✗ {a['title']} — {a['company']} · {a['reason']}" for a in d.audit]
    return "\n".join(out) + "\n"


def render(digest: Digest) -> tuple[str, str, str]:
    subject = f"📬 Stages DS — {digest.date_label} · {digest.new_count} nouvelle(s) offre(s)"
    html = _env.get_template("digest.html.j2").render(d=digest)
    return subject, html, render_text(digest)
