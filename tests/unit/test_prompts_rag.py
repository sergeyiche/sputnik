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


def test_strip_trailing_disclaimer() -> None:
    text = "Ответ по делу.\n\n---\n⚠️ Дисклеймер длинный"
    assert strip_trailing_disclaimer(text) == "Ответ по делу."


def test_build_rag_user_message_includes_guards() -> None:
    msg = build_rag_user_message("Что такое БП?", "[1] Источник: a.txt\nтекст")
    assert "только на фрагменты" in msg or "только на" in msg
    assert "Что такое БП?" in msg
    assert "a.txt" in msg
