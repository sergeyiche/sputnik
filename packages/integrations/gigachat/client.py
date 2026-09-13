"""HTTP client for GigaChat REST API using OAuth access tokens."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from packages.integrations.gigachat.auth import GigaChatAuthService, get_gigachat_auth_service
from packages.integrations.gigachat.config import GigaChatSettings
from packages.integrations.gigachat.ssl import get_httpx_verify

logger = logging.getLogger(__name__)


class GigaChatApiError(Exception):
    """Raised when GigaChat API request fails."""


class GigaChatClient:
    def __init__(
        self,
        auth: GigaChatAuthService | None = None,
        settings: GigaChatSettings | None = None,
    ) -> None:
        self._auth = auth or get_gigachat_auth_service()
        self._settings = settings or self._auth.settings

    def _http_verify(self) -> bool | str:
        return get_httpx_verify(
            verify_ssl_certs=self._settings.verify_ssl_certs,
            ca_bundle_file=self._settings.ca_bundle_file,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        token = self._auth.get_access_token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token.access_token}",
            **kwargs.pop("headers", {}),
        }
        url = f"{self._settings.api_base_url.rstrip('/')}/{path.lstrip('/')}"

        try:
            with httpx.Client(timeout=self._settings.timeout, verify=self._http_verify()) as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GigaChatApiError(f"API request failed: {exc}") from exc

        if response.status_code == 401 and retry_on_401:
            logger.warning(
                "GigaChat API 401 on %s %s — refreshing access token and retrying",
                method,
                path,
            )
            self._auth.invalidate()
            token = self._auth.get_access_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token.access_token}"
            try:
                with httpx.Client(timeout=self._settings.timeout, verify=self._http_verify()) as client:
                    response = client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise GigaChatApiError(f"API request failed after token refresh: {exc}") from exc

        if response.status_code >= 400:
            raise GigaChatApiError(
                f"API {method} {path} returned {response.status_code}: {response.text[:500]}"
            )

        return response

    def list_models(self) -> dict[str, Any]:
        """First authenticated request — list available models."""
        response = self._request("GET", "/models")
        return response.json()

    def chat_completion(self, messages: list[dict[str, str]], *, model: str | None = None) -> dict[str, Any]:
        payload = {
            "model": model or self._settings.model,
            "messages": messages,
            "temperature": 0.3,
        }
        response = self._request("POST", "/chat/completions", json=payload)
        return response.json()


def get_gigachat_client() -> GigaChatClient:
    return GigaChatClient()
