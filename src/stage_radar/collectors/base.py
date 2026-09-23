"""Contrat commun des collecteurs et client HTTP avec nouvelles tentatives."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from stage_radar.models import RawOffer

USER_AGENT = "stage-radar/0.1 (+https://github.com/Baudet-Antoine)"


class Collector(Protocol):
    name: str

    def fetch(self, since: date) -> Iterator[RawOffer]: ...


class SourceAuthError(Exception):
    """Clé absente ou refusée (401/403) : inutile de réessayer."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def get_json(client: httpx.Client, url: str, **kwargs: Any) -> Any:
    response = client.get(url, **kwargs)
    if response.status_code in (401, 403):
        raise SourceAuthError(f"HTTP {response.status_code} sur {url}")
    response.raise_for_status()
    return response.json()


def new_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


_SECRET_PARAMS = re.compile(r"((?:app_id|app_key|api_key|key|token)=)[^&\s'\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    return _SECRET_PARAMS.sub(r"\1***", text)
