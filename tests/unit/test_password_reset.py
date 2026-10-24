"""Cabinet password reset letters go through Mailtrap."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import deps
from apps.api.app.api.routes.admin import clear_password_reset_throttle
from apps.api.app.config import settings
from apps.api.app.main import app
from packages.integrations.mailtrap.client import MailtrapError, MailtrapMailer
from packages.security.admin_auth import AdminAuthService


class RecordingMailer:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.messages: list[dict[str, str]] = []

    def send(self, *, to_email: str, subject: str, text: str) -> None:
        self.messages.append({"to": to_email, "subject": subject, "text": text})


@pytest.fixture()
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mailer: RecordingMailer):
    monkeypatch.setattr(settings, "platform_db_path", str(tmp_path / "platform.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "platform_base_domain", "spootnik.ru")
    monkeypatch.setattr(settings, "password_reset_ttl_minutes", 60)
    deps.reset_platform_store()
    clear_password_reset_throttle()
    auth = AdminAuthService("admin", "secret")
    app.dependency_overrides[deps.get_admin_auth] = lambda: auth
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        deps.reset_platform_store()
        clear_password_reset_throttle()


def _token(text: str) -> str:
    match = re.search(r"#reset=(\S+)", text)
    assert match is not None
    return match.group(1)


def _approve(client: TestClient, slug: str) -> None:
    login = client.post("/v1/platform/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200, login.text
    listing = client.get("/v1/platform/tenants")
    item = next(row for row in listing.json()["items"] if row["slug"] == slug)
    approved = client.post(f"/v1/platform/tenants/{item['id']}/approve")
    assert approved.status_code == 200, approved.text


def test_owner_can_set_a_new_password_from_the_letter(client: TestClient, mailer: RecordingMailer) -> None:
    registered = client.post(
        "/v1/platform/register",
        json={"slug": "acme", "name": "Acme", "email": "owner@acme.org", "password": "password1"},
    )
    assert registered.status_code == 200, registered.text
    _approve(client, "acme")
    host = {"host": "acme.spootnik.ru"}

    forgot = client.post("/v1/admin/password/forgot", headers=host, json={"email": "owner@acme.org"})
    assert forgot.status_code == 200, forgot.text
    assert len(mailer.messages) == 1
    assert mailer.messages[0]["to"] == "owner@acme.org"
    assert "acme.spootnik.ru/admin/#reset=" in mailer.messages[0]["text"]

    reset = client.post(
        "/v1/admin/password/reset",
        json={"token": _token(mailer.messages[0]["text"]), "password": "password2"},
    )
    assert reset.status_code == 200, reset.text
    old = client.post("/v1/admin/login", headers=host, json={"username": "owner@acme.org", "password": "password1"})
    assert old.status_code == 401
    new = client.post("/v1/admin/login", headers=host, json={"username": "owner@acme.org", "password": "password2"})
    assert new.status_code == 200, new.text


def test_unknown_email_does_not_send(client: TestClient, mailer: RecordingMailer) -> None:
    client.post(
        "/v1/platform/register",
        json={"slug": "acme", "name": "Acme", "email": "owner@acme.org", "password": "password1"},
    )
    client.post(
        "/v1/platform/register",
        json={"slug": "beta", "name": "Beta", "email": "owner@beta.org", "password": "password1"},
    )
    host = {"host": "acme.spootnik.ru"}
    missing = client.post("/v1/admin/password/forgot", headers=host, json={"email": "nobody@acme.org"})
    other = client.post("/v1/admin/password/forgot", headers=host, json={"email": "owner@beta.org"})
    assert missing.status_code == 200
    assert other.status_code == 200
    assert mailer.messages == []


def test_forgot_password_is_only_on_a_tenant_host(client: TestClient) -> None:
    response = client.post("/v1/admin/password/forgot", json={"email": "owner@acme.org"})
    assert response.status_code == 404


def test_unconfigured_mailer_is_reported(client: TestClient, mailer: RecordingMailer) -> None:
    client.post(
        "/v1/platform/register",
        json={"slug": "acme", "name": "Acme", "email": "owner@acme.org", "password": "password1"},
    )
    mailer.configured = False
    response = client.post(
        "/v1/admin/password/forgot",
        headers={"host": "acme.spootnik.ru"},
        json={"email": "owner@acme.org"},
    )
    assert response.status_code == 503
    assert mailer.messages == []


def test_smtp_mailer_delivers_to_mailcatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int = 10) -> None:
            captured["address"] = (host, port, timeout)

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def send_message(self, message: object) -> None:
            captured["message"] = message

    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    sender = MailtrapMailer(
        api_key="",
        sender_email="noreply@spootnik.ru",
        sender_name="Спутник",
        use_sandbox=True,
        inbox_id="",
        transport="smtp",
        smtp_host="mailcatcher",
        smtp_port=1025,
    )
    assert sender.configured
    sender.send(to_email="owner@acme.org", subject="Тема", text="Текст")
    assert captured["address"] == ("mailcatcher", 1025, 10)
    message = captured["message"]
    assert message["To"] == "owner@acme.org"
    assert message["Subject"] == "Тема"


def test_mailer_uses_sandbox_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Address:
        def __init__(self, email: str, name: str | None = None) -> None:
            self.email = email
            self.name = name

    class Mail:
        def __init__(self, **kwargs: object) -> None:
            captured["mail"] = kwargs

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        def send(self, message: object) -> None:
            captured["sent"] = message

    monkeypatch.setitem(
        __import__("sys").modules,
        "mailtrap",
        type("mailtrap", (), {"Address": Address, "Mail": Mail, "MailtrapClient": Client}),
    )
    sender = MailtrapMailer(
        api_key="token",
        sender_email="noreply@spootnik.ru",
        sender_name="Спутник",
        use_sandbox=True,
        inbox_id="42",
    )
    assert sender.configured
    sender.send(to_email="owner@acme.org", subject="Тема", text="Текст")
    assert captured["client"] == {"token": "token", "sandbox": True, "inbox_id": "42"}
    assert not MailtrapMailer(
        api_key="",
        sender_email="noreply@spootnik.ru",
        sender_name="Спутник",
        use_sandbox=True,
        inbox_id="42",
    ).configured
    with pytest.raises(MailtrapError):
        MailtrapMailer(
            api_key="",
            sender_email="noreply@spootnik.ru",
            sender_name="Спутник",
            use_sandbox=True,
            inbox_id="",
        ).send(to_email="a@b.c", subject="s", text="t")
