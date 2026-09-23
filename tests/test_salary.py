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
