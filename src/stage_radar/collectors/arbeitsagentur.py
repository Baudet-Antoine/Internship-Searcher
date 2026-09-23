"""Collecteur Bundesagentur für Arbeit (https://jobsuche.api.bund.dev), filtre Praktikum."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import date

import httpx

from stage_radar.collectors.base import get_json, new_client
from stage_radar.models import RawOffer
from stage_radar.normalize import parse_dt, strip_html

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_PATH = "/pc/v4/app/jobs"
DETAIL_PATH = "/pc/v4/jobdetails/{code}"
HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
PRAKTIKUM_TRAINEE = 34
COUNTRIES = {"deutschland": "DE", "osterreich": "AT", "österreich": "AT", "schweiz": "CH",
             "niederlande": "NL", "belgien": "BE", "luxemburg": "LU", "frankreich": "FR"}


class ArbeitsagenturCollector:
    name = "arbeitsagentur"

    def __init__(self, queries: list[str], max_pages: int, page_size: int,
                 client: httpx.Client | None = None) -> None:
        self.queries, self.max_pages, self.page_size = queries, max_pages, page_size
        self.client = client or new_client()

    def fetch(self, since: date) -> Iterator[RawOffer]:
        days = min(100, max(1, (date.today() - since).days))
        for query in self.queries:
            for page in range(1, self.max_pages + 1):
                data = get_json(
                    self.client,
                    BA_BASE + SEARCH_PATH,
                    params={"was": query, "angebotsart": PRAKTIKUM_TRAINEE,
                            "veroeffentlichtseit": days, "page": page, "size": self.page_size},
                    headers=HEADERS,
                )
                items = data.get("stellenangebote") or []
                for item in items:
                    yield self.to_raw(item, self._description(item.get("refnr")))
                if len(items) < self.page_size:
                    break

    def _description(self, refnr: str | None) -> str | None:
        if not refnr:
            return None
        code = base64.b64encode(refnr.encode("utf-8")).decode("ascii")
        try:
            detail = get_json(self.client, BA_BASE + DETAIL_PATH.format(code=code),
                              headers=HEADERS)
        except httpx.HTTPError:
            return None
        text = detail.get("stellenangebotsBeschreibung")
        return strip_html(text) if text else None

    @staticmethod
    def to_raw(item: dict, description: str | None) -> RawOffer:
        place = item.get("arbeitsort") or {}
        refnr = item["refnr"]
        land = (place.get("land") or "").strip().lower()
        return RawOffer(
            source="arbeitsagentur",
            source_id=refnr,
            url=item.get("externeUrl")
            or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            title=strip_html(item.get("titel") or item.get("beruf")),
            company=item.get("arbeitgeber"),
            location_raw=", ".join(v for v in (place.get("ort"), place.get("land")) if v),
            country=COUNTRIES.get(land),
            city=place.get("ort"),
            description=description or strip_html(item.get("beruf")),
            description_is_full=description is not None,
            posted_at=parse_dt(item.get("aktuelleVeroeffentlichungsdatum")),
            raw=item,
        )
