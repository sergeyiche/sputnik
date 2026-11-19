"""HTTP flow: contact request through /v1/chat with a fake RAG and mailer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from apps.api.app import deps
from apps.api.app.config import settings
from apps.api.app.main import app
from packages.platform.store import PlatformStore
from packages.rag.pipeline import RAGResponse
from packages.security.disclaimer import MEDICAL_DISCLAIMER
from packages.skills.seeds import PARKINSON_WORKFLOW_SKILLS
from tests.unit.onboarding import RecordingMailer


class _LLM:
    def invoke(self, messages):
        return AIMessage(
            content=json.dumps(
                {
                    "kind": "patient",
                    "name": "Анна",
                    "organization": "",
                    "phone": "+79991234567",
                    "email": "",
                    "max": "",
                    "telegram": "",
                    "preferred_channel": "phone",
                    "topic": "консультация",
                    "summary": "Анна просит связаться по поводу консультации.",
                    "requested_action": "позвонить",
                    "urgency": "normal",
                },
                ensure_ascii=False,
            )
        )


class _Rag:
    def __init__(self) -> None:
        self.llm = _LLM()
        self.calls = 0

    def query(self, message: str, history: list | None = None, **kwargs: object) -> RAGResponse:
        self.calls += 1
        return RAGResponse(
            answer=(
                "С фондом можно связаться по почте info@fondparkinson.ru "
                "или на сайте https://fondparkinson.ru/."
            ),
            sources=[],
            chunks_used=0,
        )

    async def astream_query(self, message: str, history: list | None = None, **kwargs: object):
        self.calls += 1
        yield "С фондом можно связаться по почте info@fondparkinson.ru."
        suffix = str(kwargs.get("suffix") or "").strip()
        if suffix:
            yield f"\n\n{suffix}"
        yield f"\n\n---\n{MEDICAL_DISCLAIMER}"
        yield {"sources": [], "skills": kwargs.get("skills") or []}


class _Router:
    def match(self, tenant_id: int, skills, question: str, **kwargs: object) -> list:
        return []


@pytest.fixture()
def chat_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "platform_db_path", str(tmp_path / "platform.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "platform_base_domain", "spootnik.ru")
    deps.reset_platform_store()

    store = PlatformStore(tmp_path / "platform.db")
    tenant = store.register(
        slug="acme",
        name="Acme",
        email="info@acme.org",
        password="secret123",
    )
    store.approve(tenant.id, prompt_path=str(tmp_path / "prompt.md"))
    store.create_skill(tenant.id, PARKINSON_WORKFLOW_SKILLS[0])

    mailer = RecordingMailer()
    rag = _Rag()
    app.dependency_overrides[deps.get_rag_pipeline] = lambda: rag
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    app.dependency_overrides[deps.get_skill_router] = lambda: _Router()
    try:
        yield TestClient(app), store, tenant, mailer, rag
    finally:
        app.dependency_overrides.clear()
        deps.reset_platform_store()


def _chat(client: TestClient, message: str, session: str):
    return client.post(
        "/v1/chat",
        headers={"host": "acme.spootnik.ru", "x-session-id": session},
        json={"message": message, "stream": False},
    )


def test_direct_contact_request_sends_email(chat_client) -> None:
    client, store, tenant, mailer, rag = chat_client
    response = _chat(
        client,
        "Перезвоните мне, меня зовут Анна, +7 999 123-45-67",
        "sess-api-direct",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Наш специалист свяжется с вами в ближайшее время." in body["answer"]
    assert len(mailer.messages) == 1
    message = mailer.messages[0]
    assert message["to"] == "info@acme.org"
    assert message["attachments"][0].filename.endswith(".txt")
    assert "Перезвоните мне" in message["attachments"][0].content.decode("utf-8")
    requests = store.list_contact_requests(tenant.id)
    assert len(requests) == 1 and requests[0].status == "sent"
    assert rag.calls == 0


def test_how_to_contact_offers_then_telegram_sends(chat_client) -> None:
    client, store, tenant, mailer, rag = chat_client
    session = "sess-api-offer"
    first = _chat(client, "Как с вами связаться?", session)
    assert first.status_code == 200, first.text
    answer = first.json()["answer"]
    assert "info@fondparkinson.ru" in answer
    assert "оставьте свой телефон" in answer
    assert mailer.messages == []
    assert rag.calls == 1
    run = store.active_skill_run(tenant.id, session)
    assert run is not None and run.step == "await_contact"

    second = _chat(client, "Мой телеграм @anna_parkinson", session)
    assert second.status_code == 200, second.text
    assert "Наш специалист свяжется с вами в ближайшее время." in second.json()["answer"]
    assert len(mailer.messages) == 1
    assert "Telegram: @anna_parkinson" in mailer.messages[0]["text"]


def test_info_and_contact_in_one_message_keeps_fund_contacts(chat_client) -> None:
    client, store, tenant, mailer, rag = chat_client
    response = _chat(
        client,
        "Как связаться? Мой телефон +7 999 123-45-67",
        "sess-api-both",
    )
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "info@fondparkinson.ru" in answer
    assert "Наш специалист свяжется с вами в ближайшее время." in answer
    assert len(mailer.messages) == 1


def test_name_only_asks_for_contact_and_does_not_send(chat_client) -> None:
    client, store, tenant, mailer, _ = chat_client
    session = "sess-api-name"
    first = _chat(client, "Перезвоните мне", session)
    assert first.status_code == 200
    assert "Телефон, email, MAX или Telegram" in first.json()["answer"]

    second = _chat(client, "Меня зовут Анна", session)
    assert second.status_code == 200
    assert "Телефон, email, MAX или Telegram" in second.json()["answer"]
    assert mailer.messages == []


def test_negative_question_keeps_normal_answer(chat_client) -> None:
    client, store, tenant, mailer, rag = chat_client
    response = _chat(client, "Как связаться с врачом?", "sess-api-negative")
    assert response.status_code == 200
    assert "info@fondparkinson.ru" in response.json()["answer"]
    assert mailer.messages == []
    assert rag.calls == 1


def test_info_offer_coexists_with_a_domain_skill(chat_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, tenant, mailer, rag = chat_client
    domain = store.create_skill(
        tenant.id,
        {
            "name": "medications",
            "title": "Лекарства",
            "description": "Ответы о лекарствах",
            "body": "Только справочно.",
            "examples": ["Какие лекарства применяют?"],
        },
    )

    from packages.skills.models import MatchedSkill

    class Router:
        def match(self, tenant_id: int, skills, question: str, **kwargs: object):
            return [MatchedSkill(skill=domain, score=0.9)]

    import apps.api.app.api.routes.chat as chat_module

    monkeypatch.setattr(chat_module, "get_skill_router", lambda: Router())
    response = _chat(client, "Как с вами связаться?", "sess-api-domain")
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "info@fondparkinson.ru" in answer
    assert "оставьте свой телефон" in answer
    assert mailer.messages == []


def test_stream_info_offer_goes_before_disclaimer(chat_client) -> None:
    client, store, tenant, mailer, rag = chat_client
    response = client.post(
        "/v1/chat",
        headers={"host": "acme.spootnik.ru", "x-session-id": "sess-api-stream"},
        json={"message": "Как с вами связаться?", "stream": True},
    )
    assert response.status_code == 200
    text = response.text
    assert "info@fondparkinson.ru" in text
    assert "оставьте свой телефон" in text
    assert text.index("оставьте свой телефон") < text.index(MEDICAL_DISCLAIMER)
    assert mailer.messages == []


def test_mailer_failure_is_honest(chat_client) -> None:
    client, store, tenant, mailer, _ = chat_client
    mailer.fail = True
    response = _chat(
        client,
        "Перезвоните мне +7 999 123-45-67",
        "sess-api-fail",
    )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "Не получилось" in answer
    assert "Наш специалист свяжется" not in answer
    requests = store.list_contact_requests(tenant.id)
    assert len(requests) == 1 and requests[0].status == "failed"
