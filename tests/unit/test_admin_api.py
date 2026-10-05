"""Admin API tests with isolated dependencies (temp folders, fake store and runner)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import deps
from apps.api.app.main import app
from packages.admin.ingest_jobs import IngestJobState
from packages.admin.knowledge_files import KnowledgeFilesService
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
def client(tmp_path: Path):
    sources = tmp_path / "sources"
    knowledge = tmp_path / "knowledge"
    sources.mkdir()
    knowledge.mkdir()
    (sources / "Сон.txt").write_text("Про сон", encoding="utf-8")
    (knowledge / "Сон.txt").write_text("# source-file: Сон.txt\n\nПро сон", encoding="utf-8")

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


def _login(client: TestClient) -> None:
    response = client.post("/v1/admin/login", json={"username": "admin", "password": "secret"})
    assert response.status_code == 200


def test_endpoints_require_login(client: TestClient) -> None:
    for method, url in [("get", "/v1/admin/sources"), ("get", "/v1/admin/documents"), ("post", "/v1/admin/ingest")]:
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


def test_logout(client: TestClient) -> None:
    _login(client)
    assert client.post("/v1/admin/logout").status_code == 204
    assert client.get("/v1/admin/me").status_code == 401
