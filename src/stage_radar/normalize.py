"""Normalisation de texte et clé de dédoublonnage."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime

_LEGAL_SUFFIXES = {
    "gmbh", "ag", "se", "ltd", "limited", "llc", "inc", "bv", "nv",
    "sa", "sl", "srl", "spa", "plc", "kg", "oy", "ab", "aps",
}
_GENDER_TAGS = re.compile(
    r"\(\s*(?:[mwfdxha]\s*/\s*)+[mwfdxha]\s*\)|\(\s*all genders?\s*\)|\(\s*gn\s*\)",
    re.IGNORECASE,
)
_HTML_TAGS = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str:
    text = html.unescape(_HTML_TAGS.sub(" ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_title(title: str | None) -> str:
    return normalize_text(_GENDER_TAGS.sub(" ", title or ""))


def normalize_company(company: str | None) -> str:
    text = normalize_text((company or "").replace(".", ""))
    return " ".join(t for t in text.split() if t not in _LEGAL_SUFFIXES)


def dedup_key(company: str | None, title: str | None, country: str | None) -> str:
    base = "|".join([normalize_company(company), normalize_title(title), (country or "").upper()])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
