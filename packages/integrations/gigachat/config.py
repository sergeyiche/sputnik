"""GigaChat configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GigaChatSettings:
    authorization_key: str
    scope: str
    oauth_url: str
    api_base_url: str
    model: str
    verify_ssl_certs: bool
    ca_bundle_file: str | None
    timeout: float

    @classmethod
    def from_env(cls) -> GigaChatSettings:
        authorization_key = (
            os.getenv("GIGACHAT_AUTHORIZATION_KEY", "").strip()
            or os.getenv("AUTHORIZATION_KEY", "").strip()  # deprecated alias
            or os.getenv("GIGACHAT_CREDENTIALS", "").strip()  # deprecated alias
        )
        if not authorization_key:
            raise ValueError(
                "GIGACHAT_AUTHORIZATION_KEY не задан. "
                "Получите ключ авторизации в GigaChat Studio и добавьте в .env"
            )

        ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip() or None

        return cls(
            authorization_key=authorization_key,
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            oauth_url=os.getenv(
                "GIGACHAT_OAUTH_URL",
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            ),
            api_base_url=os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
            verify_ssl_certs=os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "true").lower() == "true",
            ca_bundle_file=ca_bundle,
            timeout=float(os.getenv("GIGACHAT_TIMEOUT", "60")),
        )
