"""Collecteur Bundesagentur für Arbeit (https://jobsuche.api.bund.dev), filtre Praktikum.

Recherche : /pc/v6/jobs (v4 renvoie 403). Détail : /pc/v4/jobdetails/{base64(referenznummer)}.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import date

import httpx

from stage_radar.collectors.base import get_json, new_client
from stage_radar.models import RawOffer
from stage_radar.normalize import parse_dt, strip_html

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_PATH = "/pc/v6/jobs"
DETAIL_PATH = "/pc/v4/jobdetails/{code}"
HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
PRAKTIKUM_TRAINEE = 34
COUNTRIES = {"deutschland": "DE", "österreich": "AT", "osterreich": "AT", "schweiz": "CH",
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
                items = data.get("ergebnisliste") or []
                for item in items:
                    yield self.to_raw(item, self._description(item.get("referenznummer")))
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
        places = item.get("stellenlokationen") or [{}]
        address = places[0].get("adresse") or {}
        refnr = item["referenznummer"]
        land = (address.get("land") or "").strip().lower()
        start = (item.get("eintrittszeitraum") or {}).get("von")
        body = description or strip_html(item.get("hauptberuf"))
        if start:
            body = f"Eintrittsdatum (start date): {start}\n{body}"
        return RawOffer(
            source="arbeitsagentur",
            source_id=refnr,
            url=f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            title=strip_html(item.get("stellenangebotsTitel") or item.get("hauptberuf")),
            company=item.get("firma"),
            location_raw=", ".join(v for v in (address.get("ort"), address.get("land")) if v),
            country=COUNTRIES.get(land),
            city=address.get("ort"),
            description=body,
            description_is_full=description is not None,
            posted_at=parse_dt((item.get("veroeffentlichungszeitraum") or {}).get("von")),
            raw=item,
        )
