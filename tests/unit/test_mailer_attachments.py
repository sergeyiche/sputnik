"""SMTP mailer: attachments and Reply-To used by contact-request."""

from __future__ import annotations

import pytest

from packages.integrations.mailtrap.client import MailAttachment, MailtrapMailer


def test_smtp_sends_attachment_and_reply_to(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int = 10) -> None:
            captured["address"] = (host, port, timeout)

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def send_message(self, message: object) -> None:
            captured["message"] = message

    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    sender = MailtrapMailer(
        api_key="",
        sender_email="noreply@spootnik.ru",
        sender_name="ИИ-Ассистент Спутник",
        use_sandbox=True,
        inbox_id="",
        transport="smtp",
        smtp_host="mailcatcher",
        smtp_port=1025,
    )
    sender.send(
        to_email="info@fondparkinson.ru",
        subject="Обращение",
        text="Текст",
        attachments=[
            MailAttachment(filename="dialog.txt", content="\ufeffДиалог".encode("utf-8"))
        ],
        reply_to="user@example.org",
    )

    message = captured["message"]
    assert message["To"] == "info@fondparkinson.ru"
    assert message["Reply-To"] == "user@example.org"
    assert "ИИ-Ассистент Спутник" in message["From"]
    parts = list(message.iter_parts())
    assert parts[0].get_content_type() == "text/plain"
    attachment = next(part for part in parts if part.get_filename())
    assert attachment.get_filename() == "dialog.txt"
    assert attachment.get_content_charset() == "utf-8"
    assert attachment.get_payload(decode=True).decode("utf-8").rstrip("\n") == "\ufeffДиалог"
    assert sender.transport == "smtp"


def test_smtp_ssl_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSmtpSsl:
        def __init__(self, host: str, port: int, timeout: int = 10) -> None:
            captured["address"] = (host, port, timeout)

        def __enter__(self) -> "FakeSmtpSsl":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def login(self, username: str, password: str) -> None:
            captured["login"] = (username, password)

        def send_message(self, message: object) -> None:
            captured["message"] = message

    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSmtpSsl)
    sender = MailtrapMailer(
        api_key="",
        sender_email="noreply@spootnik.ru",
        sender_name="ИИ-Ассистент Спутник",
        use_sandbox=True,
        inbox_id="",
        transport="smtp",
        smtp_host="postbox.cloud.yandex.net",
        smtp_port=465,
        smtp_username="postbox-user",
        smtp_password="postbox-pass",
        smtp_security="ssl",
    )
    sender.send(to_email="info@fondparkinson.ru", subject="Тема", text="Текст")
    assert captured["address"] == ("postbox.cloud.yandex.net", 465, 10)
    assert captured["login"] == ("postbox-user", "postbox-pass")
    assert captured["message"]["To"] == "info@fondparkinson.ru"
