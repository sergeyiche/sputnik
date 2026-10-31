"""Host isolation: a closed organization does not answer, and files stay apart."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import deps
from apps.api.app.config import settings
from apps.api.app.main import app
from packages.rag.pipeline import RAGResponse
from packages.rag.prompts import PROJECT_ROOT
from packages.security.admin_auth import AdminAuthService
from tests.unit.onboarding import RecordingMailer, application_token, finish_application


class _Rag:
    def query(self, message: str, history: list) -> RAGResponse:
        return RAGResponse(answer="ok", sources=[], chunks_used=0, tokens_in=3, tokens_out=4)


@pytest.fixture()
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mailer: RecordingMailer):
    monkeypatch.setattr(settings, "platform_db_path", str(tmp_path / "platform.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "platform_base_domain", "spootnik.ru")
    deps.reset_platform_store()
    auth = AdminAuthService("admin", "secret")
    app.dependency_overrides[deps.get_admin_auth] = lambda: auth
    app.dependency_overrides[deps.get_rag_pipeline] = lambda: _Rag()
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        deps.reset_platform_store()


def _register(client: TestClient, slug: str, mailer: RecordingMailer) -> None:
    finish_application(client, mailer, slug)


def _approve(client: TestClient, slug: str) -> None:
    login = client.post("/v1/platform/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200, login.text
    listing = client.get("/v1/platform/tenants")
    item = next(row for row in listing.json()["items"] if row["slug"] == slug)
    approved = client.post(f"/v1/platform/tenants/{item['id']}/approve")
    assert approved.status_code == 200, approved.text


def test_operator_console_is_on_the_landing_host(client: TestClient) -> None:
    denied = client.post(
        "/v1/platform/login",
        headers={"host": "help.spootnik.ru"},
        json={"username": "admin", "password": "secret"},
    )
    assert denied.status_code == 404
    allowed = client.post(
        "/v1/platform/login",
        headers={"host": "spootnik.ru"},
        json={"username": "admin", "password": "secret"},
    )
    assert allowed.status_code == 200, allowed.text


def test_application_reaches_the_queue_only_after_both_steps(client: TestClient, mailer: RecordingMailer) -> None:
    registered = client.post(
        "/v1/platform/register",
        json={"name": "Acme", "phone": "8 999 000 11 22", "email": "owner@acme.org"},
    )
    assert registered.status_code == 200, registered.text
    assert registered.json() == {"status": "email_sent"}
    assert "spootnik.ru/onboarding/?token=" in mailer.messages[0]["text"]
    token = application_token(mailer.messages[0]["text"])

    login = client.post("/v1/platform/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200, login.text
    assert client.get("/v1/platform/tenants").json()["items"] == []

    opened = client.get("/v1/platform/signup", params={"token": token})
    assert opened.status_code == 200, opened.text
    assert opened.json()["step"] == "organization"
    assert opened.json()["phone"] == "+7 999 000-11-22"
    reopened = client.get("/v1/platform/signup", params={"token": token})
    assert reopened.status_code == 200

    taken = client.post(
        "/v1/platform/register",
        json={"name": "Другая", "phone": "+79990001123", "email": "owner@acme.org"},
    )
    assert taken.status_code == 409
    assert len(mailer.messages) == 1

    skipped = client.post(
        "/v1/platform/signup/widget",
        json={"token": token, "widget_title": "Навигатор", "widget_description": "", "primary_color": "#fc7f03"},
    )
    assert skipped.status_code == 400

    organization = client.post(
        "/v1/platform/signup/organization",
        json={"token": token, "slug": "acme", "password": "password1", "phone": "+7 900 100-00-00"},
    )
    assert organization.status_code == 200, organization.text
    assert organization.json()["step"] == "widget"
    assert client.get("/v1/platform/tenants").json()["items"] == []

    widget = client.post(
        "/v1/platform/signup/widget",
        json={
            "token": token,
            "widget_title": "Навигатор",
            "widget_description": "Ответы по материалам",
            "primary_color": "#abc",
        },
    )
    assert widget.status_code == 200, widget.text
    assert widget.json()["status"] == "pending"
    item = client.get("/v1/platform/tenants").json()["items"][0]
    assert item["slug"] == "acme"
    assert item["phone"] == "+79001000000"
    assert item["status"] == "pending"
    prompt = Path(settings.data_root) / "tenants" / "acme" / "prompt_default.md"
    template = PROJECT_ROOT / "config" / "default_system_prompt.md"
    assert prompt.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
    _approve(client, "acme")
    approved = next(row for row in client.get("/v1/platform/tenants").json()["items"] if row["slug"] == "acme")
    assert approved["status"] == "active"


def test_failed_letter_does_not_lock_the_mailbox(client: TestClient, mailer: RecordingMailer) -> None:
    mailer.fail = True
    failed = client.post(
        "/v1/platform/register",
        json={"name": "Acme", "phone": "+79990001122", "email": "owner@acme.org"},
    )
    assert failed.status_code == 502
    mailer.fail = False
    retried = client.post(
        "/v1/platform/register",
        json={"name": "Acme", "phone": "+79990001122", "email": "owner@acme.org"},
    )
    assert retried.status_code == 200, retried.text


def test_pending_host_shows_site_but_chat_stays_closed(client: TestClient, mailer: RecordingMailer) -> None:
    _register(client, "acme", mailer)
    host = {"host": "acme.spootnik.ru"}
    preview = client.get("/v1/widget/config", headers=host)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["status"] == "pending"
    assert body["name"] == "acme"
    assert body["title"] == "acme"
    assert body["greeting"] == ""
    chat = client.post("/v1/chat", headers=host, json={"message": "привет", "stream": False})
    assert chat.status_code == 404
    cabinet = client.post(
        "/v1/admin/login",
        headers=host,
        json={"username": "acme@example.com", "password": "password1"},
    )
    assert cabinet.status_code == 200, cabinet.text


def test_starter_questions_are_isolated(client: TestClient, mailer: RecordingMailer) -> None:
    _register(client, "acme", mailer)
    _register(client, "beta", mailer)
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
    created = client.post("/v1/admin/questions", headers=acme, json={"text": "Как к вам записаться?"})
    assert created.status_code == 200, created.text
    client.post("/v1/admin/questions", headers=acme, json={"text": "Какие документы нужны?"})
    listing = client.get("/v1/admin/questions", headers=acme)
    assert {item["text"] for item in listing.json()["items"]} == {
        "Как к вам записаться?",
        "Какие документы нужны?",
    }
    shown = client.get("/v1/suggestions/starter?count=3", headers=acme)
    assert shown.status_code == 200
    assert set(shown.json()["suggestions"]) == {"Как к вам записаться?", "Какие документы нужны?"}
    client.post("/v1/admin/logout", headers=acme)

    empty = client.get("/v1/suggestions/starter?count=3", headers=beta)
    assert empty.status_code == 200
    assert empty.json()["suggestions"] == []
    other = client.post(
        "/v1/admin/login",
        headers=beta,
        json={"username": "beta@example.com", "password": "password1"},
    )
    assert other.status_code == 200, other.text
    assert client.get("/v1/admin/questions", headers=beta).json()["items"] == []
    stolen = client.put(
        f"/v1/admin/questions/{created.json()['id']}",
        headers=beta,
        json={"text": "Чужой вопрос"},
    )
    assert stolen.status_code == 404


def test_tenants_do_not_see_each_others_files(client: TestClient, mailer: RecordingMailer) -> None:
    _register(client, "acme", mailer)
    _register(client, "beta", mailer)
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


def test_question_is_counted_for_that_tenant(client: TestClient, mailer: RecordingMailer) -> None:
    _register(client, "acme", mailer)
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


def test_suspended_tenant_keeps_files_but_closes_chat(client: TestClient, mailer: RecordingMailer) -> None:
    _register(client, "acme", mailer)
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
