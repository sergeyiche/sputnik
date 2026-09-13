"""LLM provider factory — swap via LLM_PROVIDER env var."""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

from packages.integrations.gigachat.auth import get_gigachat_auth_service
from packages.integrations.gigachat.langchain_config import (
    get_gigachat_ca_bundle_file,
    get_gigachat_verify_ssl_certs,
)


def create_llm(*, access_token: str | None = None) -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "gigachat").lower()

    if provider == "gigachat":
        from langchain_gigachat import GigaChat

        auth = get_gigachat_auth_service()
        common = {
            "model": os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
            "scope": os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
            "verify_ssl_certs": get_gigachat_verify_ssl_certs(),
            "ca_bundle_file": get_gigachat_ca_bundle_file(),
            "base_url": os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            "auth_url": os.getenv(
                "GIGACHAT_OAUTH_URL",
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            ),
            "timeout": float(os.getenv("GIGACHAT_TIMEOUT", "60")),
        }

        # credentials → SDK сам обновляет токен по expiry и при 401.
        # Явный access_token — только для тестов / ручного override (без auto-refresh).
        if access_token is not None:
            return GigaChat(access_token=access_token, **common)

        return GigaChat(credentials=auth.sdk_credentials(), **common)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider}. Supported: gigachat, openai")
