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
    assert "le pipeline tourne bien" in html and "le pipeline tourne bien" in text
    d.errors = {"adzuna": "HTTP 401"}
    _, html, text = render(d)
    assert "Aucune nouvelle offre retenue" in text and "tourne bien" not in text
