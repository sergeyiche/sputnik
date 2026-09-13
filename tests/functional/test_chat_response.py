"""Functional check: chat returns a non-empty answer."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.functional
@pytest.mark.requires_gigachat
@pytest.mark.requires_knowledge
def test_chat_returns_non_empty_answer(
    client: TestClient,
    gigachat_ready: None,
    knowledge_ready: None,
) -> None:
    """POST /v1/chat должен вернуть осмысленный непустой ответ."""
    response = client.post(
        "/v1/chat",
        json={
            "message": "Что такое болезнь Паркинсона?",
            "stream": False,
        },
    )

    assert response.status_code == 200, (
        f"Ожидали HTTP 200, получили {response.status_code}: {response.text}"
    )

    payload = response.json()
    answer = payload.get("answer")

    assert isinstance(answer, str), f"answer должен быть строкой, получили: {type(answer)}"
    assert answer.strip(), "answer пустой — ассистент не вернул текст"
    assert len(answer.strip()) > 20, f"answer слишком короткий: {answer!r}"

    assert payload.get("session_id"), "session_id отсутствует"
    assert "disclaimer" in payload, "disclaimer отсутствует в ответе"
    assert isinstance(payload.get("sources"), list), "sources должен быть списком"
