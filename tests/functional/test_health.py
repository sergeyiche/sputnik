"""Functional smoke checks for API availability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.functional
def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("status") == "ok"


@pytest.mark.functional
def test_root_lists_endpoints(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert "endpoints" in payload
    assert "chat" in payload["endpoints"]
