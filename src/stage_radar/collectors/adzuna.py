"""Collecteur Adzuna (https://developer.adzuna.com). Descriptions tronquées."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import httpx

from stage_radar.collectors.base import SourceAuthError, get_json, new_client
from stage_radar.models import RawOffer
from stage_radar.normalize import parse_dt, strip_html

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaCollector:
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, countries: list[str], queries: list[str],
                 extra_queries: dict[str, list[str]], max_pages: int, results_per_page: int,
                 client: httpx.Client | None = None) -> None:
        self.app_id, self.app_key = app_id, app_key
        self.countries, self.queries, self.extra_queries = countries, queries, extra_queries
        self.max_pages, self.results_per_page = max_pages, results_per_page
        self.client = client or new_client()

    def fetch(self, since: date) -> Iterator[RawOffer]:
        if not (self.app_id and self.app_key):
            raise SourceAuthError("ADZUNA_APP_ID / ADZUNA_APP_KEY manquants")
        max_days_old = max(1, (date.today() - since).days)
        for country in self.countries:
            for query in self.queries + self.extra_queries.get(country, []):
                for page in range(1, self.max_pages + 1):
                    data = get_json(
                        self.client,
                        ADZUNA_URL.format(country=country, page=page),
                        params={
                            "app_id": self.app_id,
                            "app_key": self.app_key,
                            "what": query,
                            "max_days_old": max_days_old,
                            "results_per_page": self.results_per_page,
                            "sort_by": "date",
                            "content-type": "application/json",
                        },
                    )
                    results = data.get("results") or []
                    for item in results:
                        yield self.to_raw(item, country)
                    if len(results) < self.results_per_page:
                        break

    @staticmethod
    def to_raw(item: dict, country: str) -> RawOffer:
        location = item.get("location") or {}
        area = location.get("area") or []
        return RawOffer(
            source="adzuna",
            source_id=str(item["id"]),
            url=item.get("redirect_url", ""),
            title=strip_html(item.get("title")),
            company=(item.get("company") or {}).get("display_name"),
            location_raw=location.get("display_name", ""),
            country=country.upper(),
            city=area[-1] if len(area) >= 2 else None,
            description=strip_html(item.get("description")),
            description_is_full=False,
            posted_at=parse_dt(item.get("created")),
            raw=item,
        )
