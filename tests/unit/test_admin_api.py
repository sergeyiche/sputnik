"""Admin API tests with isolated dependencies (temp folders, fake store and runner)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import deps
from apps.api.app.main import app
from packages.admin.ingest_jobs import IngestJobState
from packages.admin.knowledge_files import KnowledgeFilesService
from packages.admin.prompt_store import reset_prompt_store
from packages.rag.prompts import _read_prompt_file
from packages.security.admin_auth import AdminAuthService


class FakeStore:
    def list_documents(self) -> list[str]:
        return ["Сон.txt"]

    def count(self) -> int:
        return 42


class FakeRunner:
    def __init__(self) -> None:
        self._state = IngestJobState()

    @property
    def state(self) -> IngestJobState:
        return self._state

    def start(self) -> IngestJobState:
        from packages.admin.ingest_jobs import IngestAlreadyRunningError

        if self._state.status == "running":
            raise IngestAlreadyRunningError("Импорт уже выполняется")
        self._state = IngestJobState(status="running", phase="preparing")
        return self._state


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sources = tmp_path / "sources"
    knowledge = tmp_path / "knowledge"
    sources.mkdir()
    knowledge.mkdir()
    (sources / "Сон.txt").write_text("Про сон", encoding="utf-8")
    (knowledge / "Сон.txt").write_text("# source-file: Сон.txt\n\nПро сон", encoding="utf-8")
    prompt_file = tmp_path / "system_prompt.md"
    prompt_file.write_text("# Заголовок\n\n---\n\nТестовый промпт по умолчанию\n", encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(prompt_file))
    monkeypatch.setenv("PROMPT_VERSIONS_DIR", str(tmp_path / "prompts"))
    reset_prompt_store()
    _read_prompt_file.cache_clear()

    auth = AdminAuthService("admin", "secret")
    runner = FakeRunner()
    app.dependency_overrides[deps.get_admin_auth] = lambda: auth
    app.dependency_overrides[deps.get_knowledge_files_service] = lambda: KnowledgeFilesService(
        sources_dir=sources, knowledge_dir=knowledge, max_upload_bytes=1024 * 1024
    )
    app.dependency_overrides[deps.get_vector_store] = lambda: FakeStore()
    app.dependency_overrides[deps.get_ingest_runner] = lambda: runner
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        reset_prompt_store()
        _read_prompt_file.cache_clear()


def _login(client: TestClient) -> None:
    response = client.post("/v1/admin/login", json={"username": "admin", "password": "secret"})
    assert response.status_code == 200


def test_endpoints_require_login(client: TestClient) -> None:
    for method, url in [
        ("get", "/v1/admin/sources"),
        ("get", "/v1/admin/documents"),
        ("post", "/v1/admin/ingest"),
        ("get", "/v1/admin/prompt"),
    ]:
        assert getattr(client, method)(url).status_code == 401


def test_wrong_password(client: TestClient) -> None:
    response = client.post("/v1/admin/login", json={"username": "admin", "password": "bad"})
    assert response.status_code == 401


def test_login_sets_httponly_cookie(client: TestClient) -> None:
    response = client.post("/v1/admin/login", json={"username": "admin", "password": "secret"})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/v1/admin" in cookie
    me = client.get("/v1/admin/me")
    assert me.status_code == 200


def test_sources_upload_convert_delete_flow(client: TestClient) -> None:
    _login(client)

    listing = client.get("/v1/admin/sources").json()
    assert listing["items"][0]["conversion_status"] == "converted"
    assert "pdf" in listing["supported_formats"]

    upload = client.post("/v1/admin/sources", files={"file": ("Питание.md", "# Питание".encode(), "text/markdown")})
    assert upload.status_code == 201
    assert upload.json()["conversion_status"] == "not_converted"

    duplicate = client.post("/v1/admin/sources", files={"file": ("Питание.md", b"x", "text/markdown")})
    assert duplicate.status_code == 409

    bad = client.post("/v1/admin/sources", files={"file": ("x.exe", b"x", "application/octet-stream")})
    assert bad.status_code == 400

    converted = client.post("/v1/admin/convert", json={"name": "Питание.md"}).json()
    assert converted["results"][0]["status"] == "created"

    deleted = client.delete("/v1/admin/sources", params={"name": "Питание.md"}).json()
    assert deleted["deleted"] == ["knowledge_sources/Питание.md", "knowledge/Питание.txt"]
    assert client.delete("/v1/admin/sources", params={"name": "Питание.md"}).status_code == 404


def test_documents_and_ingest(client: TestClient) -> None:
    _login(client)

    documents = client.get("/v1/admin/documents").json()
    assert documents["chunk_count"] == 42
    assert documents["items"][0] == {
        **documents["items"][0],
        "name": "Сон.txt",
        "in_index": True,
        "source_file": "Сон.txt",
    }

    assert client.get("/v1/admin/ingest").json()["status"] == "idle"
    assert client.post("/v1/admin/ingest").status_code == 202
    assert client.post("/v1/admin/ingest").status_code == 409


def test_prompt_save_activate_and_restore(client: TestClient, tmp_path: Path) -> None:
    _login(client)
    prompt_file = tmp_path / "system_prompt.md"
    original = prompt_file.read_text(encoding="utf-8")

    initial = client.get("/v1/admin/prompt")
    assert initial.status_code == 200
    body = initial.json()
    assert body["mode"] == "default"
    assert body["content"] == "Тестовый промпт по умолчанию"
    assert body["active_version_id"] is None
    assert body["versions"] == []

    unchanged = client.put("/v1/admin/prompt", json={"content": body["content"]})
    assert unchanged.status_code == 409

    empty = client.put("/v1/admin/prompt", json={"content": "   "})
    assert empty.status_code == 400

    saved = client.put(
        "/v1/admin/prompt",
        json={"content": "Новый промпт ассистента", "note": "первая правка"},
    )
    assert saved.status_code == 200
    saved_body = saved.json()
    assert saved_body["mode"] == "version"
    assert saved_body["content"] == "Новый промпт ассистента"
    assert saved_body["versions"][0]["note"] == "первая правка"
    assert saved_body["versions"][0]["active"] is True
    first_id = saved_body["active_version_id"]

    second = client.put("/v1/admin/prompt", json={"content": "Вторая версия промпта"})
    assert second.status_code == 200
    assert second.json()["active_version_id"] != first_id
    assert len(second.json()["versions"]) == 2

    rolled = client.post("/v1/admin/prompt/activate", json={"version_id": first_id})
    assert rolled.status_code == 200
    rolled_body = rolled.json()
    assert rolled_body["content"] == "Новый промпт ассистента"
    assert rolled_body["active_version_id"] == first_id
    assert len(rolled_body["versions"]) == 2

    assert client.post("/v1/admin/prompt/activate", json={"version_id": "missing"}).status_code == 404

    restored = client.post("/v1/admin/prompt/restore-default")
    assert restored.status_code == 200
    restored_body = restored.json()
    assert restored_body["mode"] == "default"
    assert restored_body["content"] == "Тестовый промпт по умолчанию"
    assert restored_body["active_version_id"] is None
    assert len(restored_body["versions"]) == 2
    assert prompt_file.read_text(encoding="utf-8") == original


def test_logout(client: TestClient) -> None:
    _login(client)
    assert client.post("/v1/admin/logout").status_code == 204
    assert client.get("/v1/admin/me").status_code == 401
