"""Regression checks for the longer, more conversational Parkinson prompt."""

from pathlib import Path


def test_long_answer_prompt_requires_depth_and_followup() -> None:
    text = Path("config/parkinson_prompt_v3.md").read_text(encoding="utf-8")
    assert "не вылечить болезнь Паркинсона" in text
    assert "двух-трёх содержательных абзацев" in text
    assert "Первый абзац" in text
    assert "Второй абзац" in text
    assert "Третий абзац" in text
    assert "В большинстве содержательных ответов" in text
    assert "один уместный вопрос" in text
    assert "не копируй дословно" in text.casefold()
    assert "не обещай излечение" in text.casefold()


def test_long_answer_prompt_does_not_conflict_with_short_format() -> None:
    text = Path("config/parkinson_prompt_v3.md").read_text(encoding="utf-8")
    assert "пиши лаконично" not in text.casefold()
    assert "не заканчивай каждый ответ вопросом" not in text.casefold()
    assert "если вопрос простой" in text.casefold()
