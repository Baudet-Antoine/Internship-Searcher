"""Interface commune des moteurs de décision typés."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

SCORE_ANSWERS = ("1", "2", "3", "4", "5")


@dataclass(frozen=True)
class Question:
    id: str
    label: str
    kind: Literal["bool", "choice", "score"]
    prompt: str
    choices: tuple[str, ...] = ()
    reject_answers: tuple[str, ...] = ()
    flag_answers: tuple[str, ...] = ()
    include_profile: bool = False
    flag_text: str | None = None

    @property
    def answers(self) -> tuple[str, ...]:
        if self.kind == "bool":
            return ("yes", "no")
        if self.kind == "score":
            return SCORE_ANSWERS
        return self.choices


@dataclass(frozen=True)
class Decision:
    answer: str
    p: float


class DecisionEngine(Protocol):
    name: str

    def classify(self, text: str, profile: str,
                 questions: list[Question]) -> dict[str, Decision]: ...


def load_questions(cfg: dict) -> list[Question]:
    return [
        Question(
            id=q["id"],
            label=q.get("label", q["id"]),
            kind=q["kind"],
            prompt=q.get("prompt", ""),
            choices=tuple(str(c) for c in q.get("choices", [])),
            reject_answers=tuple(str(a) for a in q.get("reject_answers", [])),
            flag_answers=tuple(str(a) for a in q.get("flag_answers", [])),
            include_profile=bool(q.get("include_profile", False)),
            flag_text=q.get("flag_text"),
        )
        for q in cfg.get("questions", [])
    ]


def engine_version(engine_name: str, cfg: dict) -> str:
    payload = json.dumps({"engine": engine_name, "cfg": cfg}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def offer_text(offer: dict, max_chars: int = 8000) -> str:
    location = ", ".join(v for v in (offer.get("city"), offer.get("country")) if v)
    return (
        f"Title: {offer.get('title', '')}\n"
        f"Company: {offer.get('company') or 'unknown'}\n"
        f"Location: {location or 'unknown'}\n\n"
        f"{(offer.get('description') or '')[:max_chars]}"
    )
