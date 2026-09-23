"""Gestion d'erreurs partagée par les étapes qui appellent un LLM."""

from __future__ import annotations

from stage_radar.collectors.base import SourceAuthError
from stage_radar.engines.gemini import GeminiQuotaError, GeminiRequestError
from stage_radar.progress import log

# Erreurs qui touchent toutes les offres (clé, quota, modèle, requête) : on s'arrête tout de suite.
FATAL_ERRORS = (GeminiQuotaError, GeminiRequestError, SourceAuthError)
# Au-delà, une erreur « par offre » est en réalité systématique : on s'arrête aussi.
MAX_CONSECUTIVE_ERRORS = 3


def stop_stage(stage: str, remaining: int, reason: str) -> None:
    log.error("%s : %s", stage, reason)
    log.warning("%s : arrêt, %d offre(s) reprise(s) au prochain passage", stage, remaining)
