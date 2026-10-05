"""Unit tests for citation normalization."""

from __future__ import annotations

from packages.rag.citations import normalize_citations, strip_citations


def test_source_label_is_reduced_to_marker() -> None:
    text, cited = normalize_citations("Выполняйте задачи последовательно. Источник: [4]", {4})
    assert text == "Выполняйте задачи последовательно[4]."
    assert cited == [4]


def test_grouped_numbers_are_split_and_unknown_dropped() -> None:
    text, cited = normalize_citations("Важен сон [1, 3, 9].", {1, 3})
    assert text == "Важен сон[1][3]."
    assert cited == [1, 3]


def test_cited_keeps_first_appearance_order() -> None:
    _, cited = normalize_citations("А [2]. Б [1]. В [2].", {1, 2})
    assert cited == [2, 1]


def test_markdown_links_are_untouched() -> None:
    text, cited = normalize_citations("См. [1](https://fondparkinson.ru/).", {1})
    assert text == "См. [1](https://fondparkinson.ru/)."
    assert cited == []


def test_strip_citations_for_history() -> None:
    assert strip_citations("Вставайте медленно [2]. Пейте воду[1][3].") == (
        "Вставайте медленно. Пейте воду."
    )
