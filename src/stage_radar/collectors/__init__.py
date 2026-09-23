from __future__ import annotations

from collections.abc import Mapping

from stage_radar.collectors.adzuna import AdzunaCollector
from stage_radar.collectors.arbeitsagentur import ArbeitsagenturCollector
from stage_radar.collectors.base import Collector


def build_collectors(search_cfg: dict, env: Mapping[str, str]) -> list[Collector]:
    collectors: list[Collector] = []
    adzuna = search_cfg.get("adzuna") or {}
    if adzuna.get("enabled"):
        collectors.append(AdzunaCollector(
            app_id=env.get("ADZUNA_APP_ID", ""),
            app_key=env.get("ADZUNA_APP_KEY", ""),
            countries=adzuna.get("countries", []),
            queries=adzuna.get("queries", []),
            extra_queries=adzuna.get("extra_queries") or {},
            max_pages=adzuna.get("max_pages", 1),
            results_per_page=adzuna.get("results_per_page", 50),
        ))
    ba = search_cfg.get("arbeitsagentur") or {}
    if ba.get("enabled"):
        collectors.append(ArbeitsagenturCollector(
            queries=ba.get("queries", []),
            max_pages=ba.get("max_pages", 1),
            page_size=ba.get("page_size", 50),
        ))
    return collectors
