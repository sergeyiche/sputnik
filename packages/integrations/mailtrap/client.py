"""Send cabinet mail through local Mailcatcher (SMTP) or Mailtrap."""

from __future__ import annotations

import base64
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _mask_email(value: str) -> str:
    local, _, domain = (value or "").partition("@")
    return f"{local[:2]}***@{domain}" if local and domain else value


class MailtrapError(Exception):
    """Mail is not configured, or the server rejected the message."""


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content: bytes
    mimetype: str = "text/plain"


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
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_security: str = "none",
    ) -> None:
        self._api_key = api_key.strip()
        self._sender_email = sender_email.strip()
        self._sender_name = sender_name.strip() or "ИИ-Ассистент Спутник"
        self._use_sandbox = use_sandbox
        self._inbox_id = inbox_id.strip()
        self._transport = transport.strip().lower()
        self._smtp_host = smtp_host.strip()
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username.strip()
        self._smtp_password = smtp_password
        security = smtp_security.strip().lower()
        self._smtp_security = security if security in {"none", "starttls", "ssl"} else "none"

    @property
    def transport(self) -> str:
        if self._transport == "smtp":
            return "smtp"
        return "mailtrap-sandbox" if self._use_sandbox else "mailtrap"

    @property
    def configured(self) -> bool:
        if not self._sender_email:
            return False
        if self._transport == "smtp":
            if self._smtp_username and not self._smtp_password:
                return False
            return bool(self._smtp_host)
        if self._transport != "mailtrap" or not self._api_key:
            return False
        if self._use_sandbox and not self._inbox_id.isdigit():
            return False
        return True

    def send(
        self,
        *,
        to_email: str,
        subject: str,
        text: str,
        attachments: list[MailAttachment] | None = None,
        reply_to: str | None = None,
    ) -> None:
        if not self.configured:
            raise MailtrapError("Отправка писем не настроена")
        files = list(attachments or [])
        try:
            if self._transport == "smtp":
                self._send_smtp(
                    to_email=to_email,
                    subject=subject,
                    text=text,
                    attachments=files,
                    reply_to=reply_to,
                )
            else:
                self._send_mailtrap(
                    to_email=to_email,
                    subject=subject,
                    text=text,
                    attachments=files,
                    reply_to=reply_to,
                )
        except MailtrapError:
            raise
        except Exception as exc:
            logger.warning("Mail delivery to %s failed: %s", to_email, exc)
            raise MailtrapError("Не удалось отправить письмо") from exc
        logger.info(
            "mail.sent transport=%s to=%s attachments=%d subject_len=%d",
            self.transport,
            _mask_email(to_email),
            len(files),
            len(subject),
        )

    def _send_smtp(
        self,
        *,
        to_email: str,
        subject: str,
        text: str,
        attachments: list[MailAttachment],
        reply_to: str | None,
    ) -> None:
        message = EmailMessage()
        message["From"] = f"{self._sender_name} <{self._sender_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(text)
        for item in attachments:
            maintype, _, subtype = item.mimetype.partition("/")
            payload: bytes | str = item.content
            params: dict[str, str] = {}
            if maintype == "text":
                try:
                    payload = item.content.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    params["charset"] = "utf-8"
            if isinstance(payload, str):
                message.add_attachment(
                    payload,
                    filename=item.filename,
                    subtype=subtype or "plain",
                    **params,
                )
            else:
                message.add_attachment(
                    payload,
                    maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=item.filename,
                )
        if self._smtp_security == "ssl":
            smtp_client = smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, timeout=10)
        else:
            smtp_client = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10)
        with smtp_client as smtp:
            if self._smtp_security == "starttls":
                smtp.starttls()
            if self._smtp_username:
                smtp.login(self._smtp_username, self._smtp_password)
            smtp.send_message(message)

    def _send_mailtrap(
        self,
        *,
        to_email: str,
        subject: str,
        text: str,
        attachments: list[MailAttachment],
        reply_to: str | None,
    ) -> None:
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
            attachments=[
                mt.Attachment(
                    content=base64.b64encode(item.content),
                    filename=item.filename,
                    mimetype=item.mimetype,
                )
                for item in attachments
            ]
            or None,
            reply_to=mt.Address(email=reply_to) if reply_to else None,
        )
        client.send(message)
