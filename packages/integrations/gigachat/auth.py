"""OAuth token acquisition for GigaChat API.

Docs: https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from packages.integrations.gigachat.config import GigaChatSettings
from packages.integrations.gigachat.ssl import get_httpx_verify
from packages.integrations.gigachat.token_store import StoredToken, get_token_store

# Unix timestamps in seconds are 10 digits until year 2286; GigaChat returns ms (13 digits).
_MS_EPOCH_THRESHOLD = 10_000_000_000


class GigaChatAuthError(Exception):
    """Raised when OAuth token request fails."""


def normalize_expires_at(expires_at: int | float | str) -> int:
    """Normalize OAuth expires_at to Unix seconds."""
    value = int(expires_at)
    if value > _MS_EPOCH_THRESHOLD:
        return value // 1000
    return value


def credentials_for_sdk(authorization_key: str) -> str:
    """Return Base64 credentials for langchain/gigachat SDK (without ``Basic `` prefix)."""
    key = authorization_key.strip()
    if key.lower().startswith("basic "):
        return key[6:].strip()
    return key


class GigaChatAuthService:
    def __init__(self, settings: GigaChatSettings | None = None) -> None:
        self._settings = settings or GigaChatSettings.from_env()
        self._store = get_token_store()

    @property
    def settings(self) -> GigaChatSettings:
        return self._settings

    def sdk_credentials(self) -> str:
        """Authorization key in the format expected by ``gigachat`` / LangChain."""
        return credentials_for_sdk(self._settings.authorization_key)

    def _http_verify(self) -> bool | str:
        return get_httpx_verify(
            verify_ssl_certs=self._settings.verify_ssl_certs,
            ca_bundle_file=self._settings.ca_bundle_file,
        )

    def _authorization_header(self) -> str:
        key = self._settings.authorization_key
        if key.lower().startswith("basic "):
            return key
        return f"Basic {key}"

    def fetch_access_token(self) -> StoredToken:
        """Request a new access token from GigaChat OAuth endpoint."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": self._authorization_header(),
        }
        data = {"scope": self._settings.scope}

        try:
            with httpx.Client(timeout=self._settings.timeout, verify=self._http_verify()) as client:
                response = client.post(
                    self._settings.oauth_url,
                    headers=headers,
                    data=data,
                )
        except httpx.HTTPError as exc:
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                hint = (
                    " Установите сертификат НУЦ: ./scripts/setup-gigachat-certs.sh "
                    "или задайте GIGACHAT_CA_BUNDLE_FILE в .env"
                )
            raise GigaChatAuthError(f"OAuth request failed: {exc}.{hint}") from exc

        if response.status_code != 200:
            raise GigaChatAuthError(
                f"OAuth returned {response.status_code}: {response.text[:500]}"
            )

        payload: dict[str, Any] = response.json()
        access_token = payload.get("access_token")
        expires_at = payload.get("expires_at")

        if not access_token or not expires_at:
            raise GigaChatAuthError(f"Unexpected OAuth response: {payload}")

        return self._store.save(str(access_token), normalize_expires_at(expires_at))

    def get_access_token(self, *, force_refresh: bool = False) -> StoredToken:
        """Return cached token or fetch a new one."""
        if not force_refresh:
            cached = self._store.get()
            if cached:
                return cached
        return self.fetch_access_token()

    def invalidate(self) -> None:
        self._store.clear()


_auth_service: GigaChatAuthService | None = None


def get_gigachat_auth_service() -> GigaChatAuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = GigaChatAuthService()
    return _auth_service
