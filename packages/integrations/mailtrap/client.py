"""Send cabinet mail through local Mailcatcher (SMTP) or Mailtrap."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class MailtrapError(Exception):
    """Mail is not configured, or the server rejected the message."""


class MailtrapMailer:
    def __init__(
        self,
        *,
        api_key: str,
        sender_email: str,
        sender_name: str,
        use_sandbox: bool,
        inbox_id: str,
        transport: str = "mailtrap",
        smtp_host: str = "",
        smtp_port: int = 1025,
    ) -> None:
        self._api_key = api_key.strip()
        self._sender_email = sender_email.strip()
        self._sender_name = sender_name.strip() or "Спутник"
        self._use_sandbox = use_sandbox
        self._inbox_id = inbox_id.strip()
        self._transport = transport.strip().lower()
        self._smtp_host = smtp_host.strip()
        self._smtp_port = smtp_port

    @property
    def configured(self) -> bool:
        if not self._sender_email:
            return False
        if self._transport == "smtp":
            return bool(self._smtp_host)
        if self._transport != "mailtrap" or not self._api_key:
            return False
        if self._use_sandbox and not self._inbox_id.isdigit():
            return False
        return True

    def send(self, *, to_email: str, subject: str, text: str) -> None:
        if not self.configured:
            raise MailtrapError("Отправка писем не настроена")
        try:
            if self._transport == "smtp":
                self._send_smtp(to_email=to_email, subject=subject, text=text)
            else:
                self._send_mailtrap(to_email=to_email, subject=subject, text=text)
        except MailtrapError:
            raise
        except Exception as exc:
            logger.warning("Mail delivery to %s failed: %s", to_email, exc)
            raise MailtrapError("Не удалось отправить письмо") from exc

    def _send_smtp(self, *, to_email: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = f"{self._sender_name} <{self._sender_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text)
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as smtp:
            smtp.send_message(message)

    def _send_mailtrap(self, *, to_email: str, subject: str, text: str) -> None:
        import mailtrap as mt

        if self._use_sandbox:
            client = mt.MailtrapClient(token=self._api_key, sandbox=True, inbox_id=self._inbox_id)
        else:
            client = mt.MailtrapClient(token=self._api_key)
        message = mt.Mail(
            sender=mt.Address(email=self._sender_email, name=self._sender_name),
            to=[mt.Address(email=to_email)],
            subject=subject,
            text=text,
        )
        client.send(message)
