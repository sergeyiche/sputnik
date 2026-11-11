"""Unit tests for prompt helpers and RAG retrieval helpers."""

from __future__ import annotations

from packages.rag.prompts import (
    _strip_markdown_preamble,
    build_rag_user_message,
    strip_trailing_disclaimer,
)


def test_strip_preamble_ignores_leading_rules() -> None:
    raw = """# Title

> comment

---

Body line one
Body line two
"""
    assert _strip_markdown_preamble(raw).startswith("Body line one")


def test_strip_preamble_keeps_headings_after_the_rule() -> None:
    raw = """# Системный промпт ассистента

> Промпт по умолчанию.

---

# РОЛЬ

Ты — «Спутник».
"""
    assert _strip_markdown_preamble(raw).startswith("# РОЛЬ")


def test_strip_trailing_disclaimer() -> None:
    text = "Ответ по делу.\n\n---\n⚠️ Дисклеймер длинный"
    assert strip_trailing_disclaimer(text) == "Ответ по делу."


def test_build_rag_user_message_includes_guards() -> None:
    msg = build_rag_user_message("Что такое БП?", "[1] Документ: «a.txt»\nтекст")
    assert "только тогда" in msg
    assert "не выдавай сведения вне контекста" in msg.lower() or "Не выдавай сведения вне контекста" in msg
    assert "Что такое БП?" in msg
    assert "a.txt" in msg
    assert "другой теме" in msg
    assert "Не добавляй факты, которых нет в контексте" not in msg


def test_build_rag_user_message_carries_topic_and_meaning() -> None:
    msg = build_rag_user_message(
        "а как их снизить?",
        "контекст",
        topic="леводопа",
        meaning="как снизить побочные эффекты леводопы",
    )
    assert "Тема диалога: леводопа" in msg
    assert "Смысл реплики с учётом диалога: как снизить побочные эффекты леводопы" in msg
    assert "Вопрос пользователя: а как их снизить?" in msg
