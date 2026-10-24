"""Host isolation: a closed organization does not answer, and files stay apart."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import deps
from apps.api.app.config import settings
from apps.api.app.main import app
from packages.rag.pipeline import RAGResponse
from packages.security.admin_auth import AdminAuthService


class _Rag:
    def query(self, message: str, history: list) -> RAGResponse:
        return RAGResponse(answer="ok", sources=[], chunks_used=0, tokens_in=3, tokens_out=4)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "platform_db_path", str(tmp_path / "platform.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "platform_base_domain", "spootnik.ru")
    deps.reset_platform_store()
    auth = AdminAuthService("admin", "secret")
    app.dependency_overrides[deps.get_admin_auth] = lambda: auth
    app.dependency_overrides[deps.get_rag_pipeline] = lambda: _Rag()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        deps.reset_platform_store()


def _register(client: TestClient, slug: str) -> None:
    response = client.post(
        "/v1/platform/register",
        json={"slug": slug, "name": slug, "email": f"{slug}@example.com", "password": "password1"},
    )
    assert response.status_code == 200, response.text


def _approve(client: TestClient, slug: str) -> None:
    login = client.post("/v1/platform/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200, login.text
    listing = client.get("/v1/platform/tenants")
    item = next(row for row in listing.json()["items"] if row["slug"] == slug)
    approved = client.post(f"/v1/platform/tenants/{item['id']}/approve")
    assert approved.status_code == 200, approved.text


def test_pending_host_does_not_answer(client: TestClient) -> None:
    _register(client, "acme")
    response = client.post(
        "/v1/chat",
        headers={"host": "acme.spootnik.ru"},
        json={"message": "привет", "stream": False},
    )
    assert response.status_code == 404


def test_tenants_do_not_see_each_others_files(client: TestClient) -> None:
    _register(client, "acme")
    _register(client, "beta")
    _approve(client, "acme")
    _approve(client, "beta")

    acme = {"host": "acme.spootnik.ru"}
    beta = {"host": "beta.spootnik.ru"}
    login = client.post(
        "/v1/admin/login",
        headers=acme,
        json={"username": "acme@example.com", "password": "password1"},
    )
    assert login.status_code == 200, login.text
    uploaded = client.post(
        "/v1/admin/sources",
        headers=acme,
        files={"file": ("note.txt", b"only acme", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text

    client.post("/v1/admin/logout", headers=acme)
    other = client.post(
        "/v1/admin/login",
        headers=beta,
        json={"username": "beta@example.com", "password": "password1"},
    )
    assert other.status_code == 200, other.text
    listing = client.get("/v1/admin/sources", headers=beta)
    assert listing.status_code == 200
    assert listing.json()["items"] == []


def test_question_is_counted_for_that_tenant(client: TestClient) -> None:
    _register(client, "acme")
    _approve(client, "acme")
    response = client.post(
        "/v1/chat",
        headers={"host": "acme.spootnik.ru"},
        json={"message": "привет", "stream": False},
    )
    assert response.status_code == 200, response.text
    listing = client.get("/v1/platform/tenants")
    item = next(row for row in listing.json()["items"] if row["slug"] == "acme")
    assert item["questions_today"] == 1
    assert item["tokens_today"] == 7


def test_suspended_tenant_keeps_files_but_closes_chat(client: TestClient) -> None:
    _register(client, "acme")
    _approve(client, "acme")
    listing = client.get("/v1/platform/tenants")
    item = next(row for row in listing.json()["items"] if row["slug"] == "acme")
    closed = client.post(f"/v1/platform/tenants/{item['id']}/enabled", json={"enabled": False})
    assert closed.status_code == 200
    chat = client.post(
        "/v1/chat",
        headers={"host": "acme.spootnik.ru"},
        json={"message": "привет", "stream": False},
    )
    assert chat.status_code == 404
    assert (Path(settings.data_root) / "tenants" / "acme").is_dir()
