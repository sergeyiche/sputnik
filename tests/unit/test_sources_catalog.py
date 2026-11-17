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


def test_tenant_catalog_uses_tenant_source_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.app import deps
    from packages.platform.context import tenant_var
    from packages.platform.store import Tenant

    knowledge = tmp_path / "tenants" / "parkinson" / "knowledge"
    sources = tmp_path / "tenants" / "parkinson" / "knowledge_sources"
    knowledge.mkdir(parents=True)
    sources.mkdir(parents=True)
    (sources / "Сон.pdf").write_bytes(b"%PDF-1.4 tenant")
    (knowledge / "Сон.txt").write_text(
        "# source-file: Сон.pdf\n# source-format: pdf\n\nтекст",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"documents": {}}', encoding="utf-8")

    monkeypatch.setattr(deps.settings, "data_root", str(tmp_path))
    monkeypatch.setenv("KNOWLEDGE_SOURCES_MANIFEST", str(manifest))
    deps._tenant_source_catalogs.pop(99, None)
    tenant = Tenant(
        id=99,
        slug="parkinson",
        name="Фонд",
        status="active",
        collection_name="parkinson_kb",
        prompt_path="",
        created_at="",
        email="f@example.org",
    )
    token = tenant_var.set(tenant)
    try:
        info = deps.get_active_source_catalog().describe("Сон.txt")
        assert info["url"] == "/v1/knowledge/sources/%D0%A1%D0%BE%D0%BD.pdf"
        assert info["format"] == "pdf"
    finally:
        tenant_var.reset(token)
        deps._tenant_source_catalogs.pop(99, None)


def test_direct_answer_source_has_download_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.app.api.routes import chat
    from packages.knowledge.base import RetrievedChunk

    knowledge = tmp_path / "knowledge"
    sources = tmp_path / "sources"
    knowledge.mkdir()
    sources.mkdir()
    (sources / "Сон.pdf").write_bytes(b"%PDF-1.4 direct")
    (knowledge / "Сон.txt").write_text(
        "# source-file: Сон.pdf\n# source-format: pdf\n\nтекст",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setenv("KNOWLEDGE_SOURCES_DIR", str(sources))

    prepared = chat.PreparedTurn(
        session_id="test-session",
        history=[],
        search_query="",
        new_topic="",
        prompt_topic="",
        prompt_meaning="",
        extra_chunks=(RetrievedChunk(content="Врачи", source="Сон.txt"),),
    )
    result = chat._direct_sources(None, prepared)
    assert result[0]["url"] == "/v1/knowledge/sources/%D0%A1%D0%BE%D0%BD.pdf"
