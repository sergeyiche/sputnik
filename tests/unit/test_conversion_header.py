"""Converter metadata must not become a standalone retrievable chunk."""

from __future__ import annotations

from pathlib import Path

from packages.etl.ingest import prepare_chunks
from packages.knowledge.base import is_conversion_header, strip_conversion_header


def test_strip_conversion_header_keeps_markdown_headings() -> None:
    raw = "# source-file: Док.docx\n# converted-at: 2026-01-01\n\n# О болезни\n\nТекст."
    assert strip_conversion_header(raw) == "# О болезни\n\nТекст."


def test_a_document_without_converter_header_is_untouched() -> None:
    raw = "# Сон\n\nТекст про сон."
    assert strip_conversion_header(raw) == raw


def test_header_only_chunk_is_detected() -> None:
    header = "# source-file: Список врачей.xlsx\n# source-format: xlsx\n# converted-at: 2026-01-01"
    assert is_conversion_header(header)
    assert not is_conversion_header(f"{header}\n\nИванов Иван\tМосква")
    assert not is_conversion_header("# О болезни\n\nТекст.")


def test_prepare_chunks_does_not_index_the_header(tmp_path: Path) -> None:
    (tmp_path / "Список врачей.txt").write_text(
        "# source-file: Список врачей.xlsx\n"
        "# source-format: xlsx\n"
        "# converted-at: 2026-01-01\n\n"
        "--- Sheet: Лист1 ---\n"
        "ФИО\tКонтакт\tГород\tЦентр\n"
        "Иванов Иван\t+7 900\tКраснодар\tКлиника\n",
        encoding="utf-8",
    )
    prepared = prepare_chunks(tmp_path)
    assert prepared.texts
    assert all(not text.lstrip().startswith("# source-file:") for text in prepared.texts)
    assert any("Иванов Иван" in text for text in prepared.texts)
