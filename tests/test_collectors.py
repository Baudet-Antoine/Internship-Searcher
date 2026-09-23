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
