"""Envoi du digest : Resend (production) ou fichier (dry-run)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

RESEND_URL = "https://api.resend.com/emails"


class Sender(Protocol):
    def send(self, subject: str, html: str, text: str) -> None: ...


class ResendSender:
    def __init__(self, api_key: str, to: str,
                 from_addr: str = "Stage Radar <onboarding@resend.dev>",
                 client: httpx.Client | None = None) -> None:
        self.api_key, self.to, self.from_addr = api_key, to, from_addr
        self.client = client or httpx.Client(timeout=30)

    def send(self, subject: str, html: str, text: str) -> None:
        response = self.client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"from": self.from_addr, "to": [self.to], "subject": subject,
                  "html": html, "text": text},
        )
        response.raise_for_status()


class FileSender:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def send(self, subject: str, html: str, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "digest.html").write_text(html, encoding="utf-8")
        (self.directory / "digest.txt").write_text(f"{subject}\n\n{text}", encoding="utf-8")
