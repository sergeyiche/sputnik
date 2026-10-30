"""Shared steps for the landing application and the two onboarding screens."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from packages.integrations.mailtrap import MailtrapError


class RecordingMailer:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.messages: list[dict[str, str]] = []
        self.fail = False

    def send(self, *, to_email: str, subject: str, text: str) -> None:
        if self.fail:
            raise MailtrapError("не доставлено")
        self.messages.append({"to": to_email, "subject": subject, "text": text})


def application_token(text: str) -> str:
    match = re.search(r"[?&]token=(\S+)", text)
    assert match is not None
    return match.group(1)


def finish_application(
    client: TestClient,
    mailer: RecordingMailer,
    slug: str,
    *,
    email: str | None = None,
    name: str | None = None,
    phone: str = "+7 (999) 000-11-22",
    password: str = "password1",
) -> None:
    email = email or f"{slug}@example.com"
    name = name or slug
    registered = client.post(
        "/v1/platform/register",
        json={"name": name, "phone": phone, "email": email},
    )
    assert registered.status_code == 200, registered.text
    token = application_token(mailer.messages[-1]["text"])
    opened = client.get("/v1/platform/signup", params={"token": token})
    assert opened.status_code == 200, opened.text
    organization = client.post(
        "/v1/platform/signup/organization",
        json={"token": token, "slug": slug, "password": password},
    )
    assert organization.status_code == 200, organization.text
    widget = client.post(
        "/v1/platform/signup/widget",
        json={
            "token": token,
            "widget_title": name,
            "widget_description": "Ответы",
            "primary_color": "#fc7f03",
        },
    )
    assert widget.status_code == 200, widget.text
    assert widget.json()["status"] == "pending"
