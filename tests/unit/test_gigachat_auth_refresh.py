"""Unit tests for GigaChat token expiry / credentials helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from packages.integrations.gigachat.auth import (
    credentials_for_sdk,
    normalize_expires_at,
)
from packages.integrations.gigachat.client import GigaChatApiError, GigaChatClient
from packages.integrations.gigachat.errors import is_gigachat_auth_error
from packages.integrations.gigachat.token_store import StoredToken


def test_normalize_expires_at_milliseconds() -> None:
    ms = 1_725_000_000_000
    assert normalize_expires_at(ms) == ms // 1000


def test_normalize_expires_at_seconds() -> None:
    seconds = 1_725_000_000
    assert normalize_expires_at(seconds) == seconds


def test_credentials_for_sdk_strips_basic_prefix() -> None:
    assert credentials_for_sdk("Basic abc123") == "abc123"
    assert credentials_for_sdk("abc123") == "abc123"


def test_stored_token_expires_with_seconds() -> None:
    past = int(time.time()) - 10
    assert not StoredToken("tok", past).is_valid(buffer_seconds=0)
    future = int(time.time()) + 600
    assert StoredToken("tok", future).is_valid()


def test_is_gigachat_auth_error_detects_401() -> None:
    class Fake:
        status_code = 401

    assert is_gigachat_auth_error(Fake())
    assert is_gigachat_auth_error(Exception("401 Unauthorized from API"))
    assert not is_gigachat_auth_error(Exception("timeout"))


def test_client_retries_once_on_401() -> None:
    auth = MagicMock()
    auth.get_access_token.side_effect = [
        StoredToken("old-token", int(time.time()) + 100),
        StoredToken("new-token", int(time.time()) + 100),
    ]
    auth.settings.api_base_url = "https://api.example.test/v1"
    auth.settings.timeout = 5.0
    auth.settings.verify_ssl_certs = False
    auth.settings.ca_bundle_file = None

    client = GigaChatClient(auth=auth)

    unauthorized = httpx.Response(401, text="expired", request=httpx.Request("GET", "https://api.example.test/v1/models"))
    ok = httpx.Response(
        200,
        json={"data": []},
        request=httpx.Request("GET", "https://api.example.test/v1/models"),
    )

    mock_http = MagicMock()
    mock_http.__enter__.return_value = mock_http
    mock_http.__exit__.return_value = False
    mock_http.request.side_effect = [unauthorized, ok]

    with patch("packages.integrations.gigachat.client.httpx.Client", return_value=mock_http):
        payload = client.list_models()

    assert payload == {"data": []}
    auth.invalidate.assert_called_once()
    assert auth.get_access_token.call_count == 2
    assert auth.get_access_token.call_args_list[1].kwargs.get("force_refresh") is True


def test_client_raises_after_failed_retry() -> None:
    auth = MagicMock()
    auth.get_access_token.return_value = StoredToken("tok", int(time.time()) + 100)
    auth.settings.api_base_url = "https://api.example.test/v1"
    auth.settings.timeout = 5.0
    auth.settings.verify_ssl_certs = False
    auth.settings.ca_bundle_file = None

    client = GigaChatClient(auth=auth)
    unauthorized = httpx.Response(401, text="expired", request=httpx.Request("GET", "https://x/v1/models"))

    mock_http = MagicMock()
    mock_http.__enter__.return_value = mock_http
    mock_http.__exit__.return_value = False
    mock_http.request.side_effect = [unauthorized, unauthorized]

    with patch("packages.integrations.gigachat.client.httpx.Client", return_value=mock_http):
        with pytest.raises(GigaChatApiError, match="401"):
            client.list_models()
