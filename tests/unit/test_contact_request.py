"""Summary, transcript, email composition and the send tool."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from packages.dialogue.store import DialogueStore
from packages.platform.store import PlatformStore
from packages.skills.contact_request import (
    build_transcript,
    compose_contact_email,
    send_contact_request_tool,
    summarize_contact_request,
)
from packages.skills.contacts import ContactSlots, extract_contacts
from packages.skills.seeds import PARKINSON_WORKFLOW_SKILLS
from packages.skills.tools import ToolContext, call_tool
from packages.skills.workflow import plan_workflow_turn


class FakeLLM:
    def __init__(self, payload: dict | None = None, *, fail: bool = False, raw: str | None = None):
        self.payload = payload or {}
        self.fail = fail
        self.raw = raw
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm down")
        if self.raw is not None:
            return AIMessage(content=self.raw)
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


class FakeMailer:
    def __init__(self) -> None:
        self.configured = True
        self.fail = False
        self.messages: list[dict] = []

    def send(self, *, to_email: str, subject: str, text: str, attachments=None, reply_to=None) -> None:
        if self.fail:
            raise RuntimeError("smtp down")
        self.messages.append(
            {
                "to_email": to_email,
                "subject": subject,
                "text": text,
                "attachments": list(attachments or []),
                "reply_to": reply_to,
            }
        )


def _setup(tmp_path: Path, *, email: str = "info@acme.org"):
    store = PlatformStore(tmp_path / "platform.db")
    dialogue = DialogueStore(tmp_path / "platform.db")
    tenant = store.register(slug="acme", name="Acme", email=email, password="secret123")
    skill = store.create_skill(tenant.id, PARKINSON_WORKFLOW_SKILLS[0])
    return store, dialogue, tenant, skill


def _action(store: PlatformStore, tenant_id: int, skill, question: str, session: str):
    turn = plan_workflow_turn(
        store=store,
        tenant_id=tenant_id,
        session_id=session,
        question=question,
        skill=skill,
    )
    assert turn is not None and turn.action is not None
    return turn


def _context(store, dialogue, tenant, skill, session, question, llm, mailer):
    return ToolContext(
        tenant=tenant,
        tenant_id=tenant.id,
        session_id=session,
        dialogue=dialogue,
        llm=llm,
        mailer=mailer,
        store=store,
        skill=skill,
        question=question,
        config=skill.config if isinstance(skill.config, dict) else {},
    )


def _summary_payload() -> dict:
    return {
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
    }


def test_build_transcript_includes_history_current_turn_and_bom(tmp_path: Path) -> None:
    store, dialogue, tenant, _ = _setup(tmp_path)
    session = "sess-transcript"
    dialogue.record_exchange(
        session,
        tenant_id=tenant.id,
        user_text="Здравствуйте",
        answer_text="Здравствуйте! Чем помочь?",
        sources=[{"title": "О фонде"}],
        topic="",
    )

    transcript = build_transcript(
        dialogue=dialogue,
        session_id=session,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        current_question="Перезвоните мне",
        confirmation="Наш специалист свяжется с вами в ближайшее время.",
    )
    assert transcript.filename.endswith(".txt")
    assert "Здравствуйте" in transcript.text
    assert "Перезвоните мне" in transcript.text
    assert "Наш специалист свяжется" in transcript.text
    assert "[источники: О фонде]" in transcript.text
    assert transcript.content.startswith(b"\xef\xbb\xbf")


def test_build_transcript_truncates_large_dialogue(tmp_path: Path) -> None:
    store, dialogue, tenant, _ = _setup(tmp_path)
    session = "sess-long"
    dialogue.record_exchange(
        session,
        tenant_id=tenant.id,
        user_text="а" * 5000,
        answer_text="б" * 5000,
        sources=[],
        topic="",
    )
    transcript = build_transcript(
        dialogue=dialogue,
        session_id=session,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        current_question="в" * 5000,
        confirmation="г" * 5000,
        max_bytes=4000,
    )
    assert transcript.truncated
    assert "часть диалога опущена" in transcript.text
    assert transcript.size <= 4200


def test_summary_uses_llm_then_falls_back(tmp_path: Path) -> None:
    slots = extract_contacts("Перезвоните мне, меня зовут Анна, +7 999 123-45-67")
    llm = FakeLLM(_summary_payload())
    summary = summarize_contact_request(llm=llm, transcript="диалог", slots=slots)
    assert summary.source == "llm"
    assert summary.summary == "Анна просит связаться по поводу консультации."
    assert summary.phone == slots.phone

    broken = summarize_contact_request(llm=FakeLLM(raw="не json"), transcript="диалог", slots=slots)
    assert broken.source == "fallback"
    assert broken.kind == slots.kind

    down = summarize_contact_request(llm=FakeLLM(fail=True), transcript="диалог", slots=slots)
    assert down.source == "fallback"
    assert down.phone == slots.phone


def test_compose_email_marks_assistant_and_includes_channels(tmp_path: Path) -> None:
    store, dialogue, tenant, _ = _setup(tmp_path)
    slots = ContactSlots(
        name="Анна",
        organization="ООО «Ромашка»",
        phone="+79991234567",
        telegram="@anna",
        kind="partnership",
        preferred_channel="telegram",
    )
    summary = summarize_contact_request(
        llm=FakeLLM(
            {
                **_summary_payload(),
                "kind": "partnership",
                "organization": "ООО «Ромашка»",
                "summary": "Предложение о сотрудничестве.",
            }
        ),
        transcript="диалог",
        slots=slots,
    )
    to_email, subject, body = compose_contact_email(
        tenant=tenant,
        request_id="req123",
        slots=slots,
        summary=summary,
        transcript_name="dialog.txt",
        session_id="sess-email",
    )
    assert to_email == "info@acme.org"
    assert "сотрудничество" in subject
    assert "Сообщение сформировано ассистентом" in body
    assert "ООО «Ромашка»" in body
    assert "Telegram: @anna" in body
    assert "Email:" not in body
    assert "MAX:" not in body


def test_send_tool_success(tmp_path: Path) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    session = "sess-tool-ok"
    question = "Перезвоните мне, меня зовут Анна, +7 999 123-45-67"
    turn = _action(store, tenant.id, skill, question, session)
    mailer = FakeMailer()
    result = send_contact_request_tool(
        payload=turn.action.payload,
        context=_context(store, dialogue, tenant, skill, session, question, FakeLLM(_summary_payload()), mailer),
    )
    assert result.ok
    assert result.message == "Наш специалист свяжется с вами в ближайшее время."
    assert len(mailer.messages) == 1
    message = mailer.messages[0]
    assert message["to_email"] == "info@acme.org"
    assert message["attachments"][0].filename.endswith(".txt")
    assert "Перезвоните мне" in message["attachments"][0].content.decode("utf-8")
    requests = store.list_contact_requests(tenant.id)
    assert len(requests) == 1 and requests[0].status == "sent"
    assert store.active_skill_run(tenant.id, session) is None


def test_send_tool_failure_is_reported(tmp_path: Path) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    session = "sess-tool-fail"
    question = "Перезвоните мне +7 999 123-45-67"
    turn = _action(store, tenant.id, skill, question, session)
    mailer = FakeMailer()
    mailer.fail = True
    result = send_contact_request_tool(
        payload=turn.action.payload,
        context=_context(store, dialogue, tenant, skill, session, question, FakeLLM(_summary_payload()), mailer),
    )
    assert not result.ok
    assert "Не получилось" in result.message
    requests = store.list_contact_requests(tenant.id)
    assert len(requests) == 1 and requests[0].status == "failed"
    assert mailer.messages == []


def test_send_tool_requires_valid_account_email(tmp_path: Path) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    session = "sess-tool-norecipient"
    question = "Перезвоните мне +7 999 123-45-67"
    turn = _action(store, tenant.id, skill, question, session)
    mailer = FakeMailer()
    bad_tenant = replace(tenant, email="help")
    result = send_contact_request_tool(
        payload=turn.action.payload,
        context=_context(store, dialogue, bad_tenant, skill, session, question, FakeLLM(_summary_payload()), mailer),
    )
    assert not result.ok
    assert result.error == "no_recipient"
    assert mailer.messages == []


def test_duplicate_request_is_suppressed(tmp_path: Path) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    session = "sess-duplicate"
    question = "Перезвоните мне +7 999 123-45-67"
    turn = _action(store, tenant.id, skill, question, session)
    mailer = FakeMailer()
    llm = FakeLLM(_summary_payload())
    first = send_contact_request_tool(
        payload=turn.action.payload,
        context=_context(store, dialogue, tenant, skill, session, question, llm, mailer),
    )
    assert first.ok and not first.duplicate

    again = _action(store, tenant.id, skill, "Перезвоните мне ещё раз +7 999 123-45-67", session)
    second = send_contact_request_tool(
        payload=again.action.payload,
        context=_context(store, dialogue, tenant, skill, session, question, llm, mailer),
    )
    assert second.ok and second.duplicate
    assert len(mailer.messages) == 1


def test_allowed_tools_is_enforced(tmp_path: Path) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    restricted = replace(skill, allowed_tools=[])
    with pytest.raises(PermissionError):
        call_tool(
            "send_contact_request",
            skill=restricted,
            payload={},
            context=_context(store, dialogue, tenant, restricted, "s", "q", FakeLLM(), FakeMailer()),
        )


def test_send_success_is_logged_without_contacts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store, dialogue, tenant, skill = _setup(tmp_path)
    session = "sess-log"
    question = "Перезвоните мне +7 999 123-45-67"
    turn = _action(store, tenant.id, skill, question, session)
    mailer = FakeMailer()
    with caplog.at_level(logging.INFO):
        send_contact_request_tool(
            payload=turn.action.payload,
            context=_context(store, dialogue, tenant, skill, session, question, FakeLLM(_summary_payload()), mailer),
        )
    assert "contact_request.sent" in caplog.text
    assert "+79991234567" not in caplog.text


def test_compose_email_with_max_contact(tmp_path: Path) -> None:
    store, dialogue, tenant, _ = _setup(tmp_path)
    slots = ContactSlots(max="@anna", kind="patient", preferred_channel="max")
    summary = summarize_contact_request(llm=FakeLLM(_summary_payload()), transcript="диалог", slots=slots)
    _, _, body = compose_contact_email(
        tenant=tenant,
        request_id="req-max",
        slots=slots,
        summary=summary,
        transcript_name="dialog.txt",
        session_id="sess-max",
    )
    assert "MAX: @anna" in body
    assert "Telegram:" not in body
    assert "Телефон:" not in body
    assert "Удобный канал: MAX" in body


def test_summary_org_fills_email_when_slots_have_only_channel(tmp_path: Path) -> None:
    store, dialogue, tenant, _ = _setup(tmp_path)
    slots = ContactSlots(email="ivan@example.org", kind="partnership")
    summary = summarize_contact_request(
        llm=FakeLLM(
            {
                **_summary_payload(),
                "kind": "partnership",
                "name": "Иван",
                "organization": "ООО «Ромашка»",
                "summary": "Предложение о сотрудничестве.",
            }
        ),
        transcript="диалог",
        slots=slots,
    )
    _, subject, body = compose_contact_email(
        tenant=tenant,
        request_id="req-org",
        slots=slots,
        summary=summary,
        transcript_name="dialog.txt",
        session_id="sess-org",
    )
    assert "ООО «Ромашка»" in subject
    assert "Имя: Иван" in body
    assert "Организация: ООО «Ромашка»" in body
