"""Unit tests for knowledge base file management."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from packages.admin.knowledge_files import (
    FileExistsConflictError,
    FileMissingError,
    FileTooLargeError,
    InvalidFileError,
    KnowledgeFilesService,
)


@pytest.fixture()
def service(tmp_path: Path) -> KnowledgeFilesService:
    sources = tmp_path / "sources"
    knowledge = tmp_path / "knowledge"
    sources.mkdir()
    knowledge.mkdir()

    (sources / "Сон.txt").write_text("Про сон", encoding="utf-8")
    (sources / "Навигатор.md").write_text("# Навигатор", encoding="utf-8")
    (sources / "Старый.doc").write_bytes(b"legacy")
    (sources / "README.md").write_text("служебный", encoding="utf-8")

    renamed = knowledge / "Проекты фонда.txt"
    renamed.write_text("# source-file: Навигатор.md\n# converted-at: 2026-01-01T00:00:00+00:00\n\nстарое", encoding="utf-8")
    old = os.path.getmtime(sources / "Навигатор.md") - 100
    os.utime(renamed, (old, old))

    (knowledge / "О фонде.txt").write_text("Вручную", encoding="utf-8")
    return KnowledgeFilesService(sources_dir=sources, knowledge_dir=knowledge, max_upload_bytes=1024)


def _by_name(items, name):
    return next(item for item in items if item.name == name)


def test_list_sources_statuses(service: KnowledgeFilesService) -> None:
    items = service.list_sources()
    assert [i.name for i in items] == ["Навигатор.md", "Сон.txt", "Старый.doc"]
    assert _by_name(items, "Сон.txt").conversion_status == "not_converted"
    assert _by_name(items, "Навигатор.md").conversion_status == "outdated"
    assert _by_name(items, "Навигатор.md").document == "Проекты фонда.txt"
    assert _by_name(items, "Старый.doc").conversion_status == "unsupported"


def test_convert_overwrites_renamed_document(service: KnowledgeFilesService, tmp_path: Path) -> None:
    result = service.convert_source("Навигатор.md")
    assert result.status == "updated"
    assert result.output.name == "Проекты фонда.txt"
    assert not (tmp_path / "knowledge" / "Навигатор.txt").exists()
    assert _by_name(service.list_sources(), "Навигатор.md").conversion_status == "converted"


def test_convert_pending_skips_up_to_date(service: KnowledgeFilesService) -> None:
    first = {r.source.name: r.status for r in service.convert_pending()}
    assert first == {"Навигатор.md": "updated", "Сон.txt": "created"}
    second = {r.source.name: r.status for r in service.convert_pending()}
    assert set(second.values()) == {"skipped"}


def test_convert_unsupported_format(service: KnowledgeFilesService) -> None:
    with pytest.raises(InvalidFileError):
        service.convert_source("Старый.doc")


def test_upload_validation(service: KnowledgeFilesService) -> None:
    item = service.save_upload("../../Новый.txt", io.BytesIO(b"text"))
    assert item.name == "Новый.txt"

    with pytest.raises(FileExistsConflictError):
        service.save_upload("Новый.txt", io.BytesIO(b"text"))
    assert service.save_upload("Новый.txt", io.BytesIO(b"text2"), overwrite=True).size_bytes == 5

    with pytest.raises(InvalidFileError):
        service.save_upload("virus.exe", io.BytesIO(b"x"))
    with pytest.raises(InvalidFileError):
        service.save_upload("README.md", io.BytesIO(b"x"))
    with pytest.raises(InvalidFileError):
        service.save_upload("empty.txt", io.BytesIO(b""))
    with pytest.raises(FileTooLargeError):
        service.save_upload("big.txt", io.BytesIO(b"x" * 2048))


def test_upload_failure_leaves_no_temp_files(service: KnowledgeFilesService, tmp_path: Path) -> None:
    with pytest.raises(FileTooLargeError):
        service.save_upload("big.txt", io.BytesIO(b"x" * 2048))
    assert not list((tmp_path / "sources").glob(".upload-*"))
    assert not (tmp_path / "sources" / "big.txt").exists()


def test_delete_removes_source_and_document(service: KnowledgeFilesService, tmp_path: Path) -> None:
    deleted = service.delete_source("Навигатор.md")
    assert deleted == ["knowledge_sources/Навигатор.md", "knowledge/Проекты фонда.txt"]
    assert not (tmp_path / "knowledge" / "Проекты фонда.txt").exists()


def test_delete_rejects_traversal_and_missing(service: KnowledgeFilesService) -> None:
    with pytest.raises(InvalidFileError):
        service.delete_source("../knowledge/О фонде.txt")
    with pytest.raises(FileMissingError):
        service.delete_source("Нет такого.pdf")
    with pytest.raises(FileMissingError):
        service.delete_source("README.md")


def test_list_documents(service: KnowledgeFilesService) -> None:
    items = service.list_documents(indexed={"О фонде.txt"})
    nav = _by_name(items, "Проекты фонда.txt")
    assert nav.source_file == "Навигатор.md"
    assert nav.converted_at is not None and nav.converted_at.year == 2026
    assert not nav.in_index

    manual = _by_name(items, "О фонде.txt")
    assert manual.converted_at is None and manual.in_index
