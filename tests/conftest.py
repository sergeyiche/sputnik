"""Pytest fixtures for functional API tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _has_gigachat_key() -> bool:
    return bool(
        os.getenv("GIGACHAT_AUTHORIZATION_KEY", "").strip()
        or os.getenv("AUTHORIZATION_KEY", "").strip()
        or os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    )


@pytest.fixture(scope="session")
def client() -> TestClient:
    from apps.api.app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def gigachat_ready() -> None:
    if not _has_gigachat_key():
        pytest.skip("GIGACHAT_AUTHORIZATION_KEY не задан в .env")


@pytest.fixture(scope="session")
def knowledge_ready(client: TestClient) -> None:
    response = client.get("/v1/knowledge/status")
    if response.status_code != 200:
        pytest.skip(f"knowledge/status недоступен: HTTP {response.status_code}")
    data = response.json()
    if int(data.get("chunk_count", 0)) <= 0:
        pytest.skip("База знаний пуста — сначала запустите ./scripts/ingest.sh --recreate")
