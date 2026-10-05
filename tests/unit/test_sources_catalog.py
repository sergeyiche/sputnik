"""Unit tests for mapping indexed documents to downloadable originals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.knowledge.sources import SourceCatalog


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    knowledge = tmp_path / "knowledge"
    sources = tmp_path / "sources"
    knowledge.mkdir()
    sources.mkdir()

    (sources / "Сон.pdf").write_bytes(b"%PDF-1.4 test")
    (knowledge / "Сон.txt").write_text("# source-file: Сон.pdf\n# source-format: pdf\n\nтекст", encoding="utf-8")

    (sources / "Внутреннее.docx").write_bytes(b"docx")
    (knowledge / "Внутреннее.txt").write_text("# source-file: Внутреннее.docx\n\nтекст", encoding="utf-8")

    (knowledge / "О фонде.txt").write_text("Фонд создан…", encoding="utf-8")
    (sources / "secret.pdf").write_bytes(b"not linked")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "documents": {
                    "Сон.txt": {"title": "Сон при болезни Паркинсона"},
                    "Внутреннее.txt": {"downloadable": False},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return knowledge, sources, manifest


def _catalog(dirs: tuple[Path, Path, Path]) -> SourceCatalog:
    knowledge, sources, manifest = dirs
    return SourceCatalog(knowledge_dir=knowledge, sources_dir=sources, manifest_path=manifest)


def test_resolves_original_from_header(dirs) -> None:
    info = _catalog(dirs).describe("Сон.txt")
    assert info["title"] == "Сон при болезни Паркинсона"
    assert info["format"] == "pdf"
    assert info["size_bytes"] == len(b"%PDF-1.4 test")
    assert info["url"] == "/v1/knowledge/sources/%D0%A1%D0%BE%D0%BD.pdf"


def test_document_without_original_has_no_url(dirs) -> None:
    info = _catalog(dirs).describe("О фонде.txt")
    assert info["title"] == "О фонде"
    assert info["url"] is None


def test_manifest_can_disable_download(dirs) -> None:
    catalog = _catalog(dirs)
    assert catalog.describe("Внутреннее.txt")["url"] is None
    assert catalog.downloadable_path("Внутреннее.docx") is None


def test_only_linked_files_are_downloadable(dirs) -> None:
    catalog = _catalog(dirs)
    assert catalog.downloadable_path("Сон.pdf") is not None
    assert catalog.downloadable_path("secret.pdf") is None


def test_path_traversal_is_rejected(dirs) -> None:
    assert _catalog(dirs).downloadable_path("../manifest.json") is None
