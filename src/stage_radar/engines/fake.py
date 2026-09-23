"""Moteur de test : répond « tout passe » sauf réponses imposées."""

from __future__ import annotations

from stage_radar.engines.base import Decision, Question


def _default_answer(question: Question) -> str:
    if question.kind == "score":
        return "3"
    blocked = set(question.reject_answers) | set(question.flag_answers)
    return next(a for a in question.answers if a not in blocked)


class FakeEngine:
    name = "fake"

    def __init__(self, answers: dict[str, Decision] | None = None) -> None:
        self.answers = answers or {}

    def classify(self, text: str, profile: str,
                 questions: list[Question]) -> dict[str, Decision]:
        return {q.id: self.answers.get(q.id, Decision(_default_answer(q), 0.9))
                for q in questions}
