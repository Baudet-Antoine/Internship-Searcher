import json

import httpx
import pytest
import respx

from stage_radar.config import load_yaml
from stage_radar.engines.base import Decision, engine_version, load_questions, offer_text
from stage_radar.engines.fake import FakeEngine
from stage_radar.engines.gemini import (
    GEMINI_URL,
    GeminiClient,
    GeminiEngine,
    GeminiQuotaError,
    GeminiRequestError,
)
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
def test_gemini_client_client_error_is_permanent_and_explained():
    body = {"error": {"code": 404, "message": "models/m is not found for API version v1beta"}}
    route = respx.post(GEMINI_URL.format(model="m")).mock(
        return_value=httpx.Response(404, json=body)
    )
    client = GeminiClient("key", "m", sleep=lambda s: None)
    with pytest.raises(GeminiRequestError, match="HTTP 404.*models/m is not found"):
        client.generate_json("sys", "prompt", {})
    assert route.call_count == 1


@respx.mock
def test_gemini_quota_error_carries_api_message():
    body = {"error": {"code": 429, "message": "Quota exceeded: requests per day"}}
    respx.post(GEMINI_URL.format(model="m")).mock(return_value=httpx.Response(429, json=body))
    client = GeminiClient("key", "m", sleep=lambda s: None)
    with pytest.raises(GeminiQuotaError, match="requests per day"):
        client.generate_json("sys", "prompt", {})


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
