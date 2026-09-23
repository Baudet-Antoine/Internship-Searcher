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


class GeminiRequestError(Exception):
    """Erreur 4xx permanente (modèle introuvable, requête invalide) : inutile de continuer."""


def _api_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:300]


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
            code = response.status_code
            if code == 429:
                if last:
                    raise GeminiQuotaError(
                        f"quota Gemini atteint (HTTP 429) : {_api_message(response)}")
                self.sleep(30)
                continue
            if code in (401, 403):
                raise SourceAuthError(f"Gemini HTTP {code} : {_api_message(response)}")
            if 400 <= code < 500:
                raise GeminiRequestError(f"Gemini HTTP {code} : {_api_message(response)}")
            if code >= 500 and not last:
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
