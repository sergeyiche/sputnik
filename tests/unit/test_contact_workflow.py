"""State machine for the contact-request workflow."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.platform.store import PlatformStore
from packages.skills.seeds import PARKINSON_WORKFLOW_SKILLS
from packages.skills.workflow import (
    CONTACT_REQUEST_NAME,
    find_contact_skill,
    plan_workflow_turn,
)


def _store(tmp_path: Path) -> PlatformStore:
    return PlatformStore(tmp_path / "platform.db")


def _tenant(store: PlatformStore):
    return store.register(
        slug="acme",
        name="Acme",
        email="info@acme.org",
        password="secret123",
    )


def _skill(store: PlatformStore, tenant_id: int):
    return store.create_skill(tenant_id, PARKINSON_WORKFLOW_SKILLS[0])


def _plan(store: PlatformStore, tenant_id: int, session: str, question: str, skill=None):
    return plan_workflow_turn(
        store=store,
        tenant_id=tenant_id,
        session_id=session,
        question=question,
        skill=skill,
    )


def test_find_contact_skill_and_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    assert find_contact_skill(store, tenant.id) == skill

    monkeypatch.setenv("CONTACT_REQUEST_ENABLED", "false")
    assert find_contact_skill(store, tenant.id) is None

    monkeypatch.setenv("CONTACT_REQUEST_ENABLED", "true")
    monkeypatch.setenv("SKILLS_ENABLED", "false")
    assert find_contact_skill(store, tenant.id) is None


def test_info_question_offers_contact_without_action(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    turn = _plan(store, tenant.id, "sess-info-1", "Как с вами связаться?", skill)

    assert turn is not None
    assert turn.mode == "offer"
    assert turn.step == "await_contact"
    assert turn.action is None
    assert "телефон" in turn.answer_suffix and "Telegram" in turn.answer_suffix
    run = store.active_skill_run(tenant.id, "sess-info-1")
    assert run is not None and run.step == "await_contact"
    assert store.list_contact_requests(tenant.id) == []


def test_contact_after_offer_sends(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-info-2"
    _plan(store, tenant.id, session, "Как с вами связаться?", skill)

    turn = _plan(store, tenant.id, session, "Мой телеграм @anna_parkinson", skill)
    assert turn is not None
    assert turn.mode == "direct"
    assert turn.step == "send"
    assert turn.action is not None
    assert turn.action.tool == "send_contact_request"
    assert turn.slots.telegram == "@anna_parkinson"
    assert turn.answer == "Наш специалист свяжется с вами в ближайшее время."


def test_request_without_contact_asks_then_sends(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-collect-1"

    first = _plan(store, tenant.id, session, "Перезвоните мне", skill)
    assert first is not None
    assert first.step == "collect_contacts"
    assert first.action is None
    assert "Телефон" in first.answer

    second = _plan(store, tenant.id, session, "+7 999 123-45-67", skill)
    assert second is not None
    assert second.step == "send"
    assert second.action is not None
    assert second.slots.phone == "+79991234567"


def test_request_with_contact_sends_immediately(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    turn = _plan(
        store,
        tenant.id,
        "sess-send-1",
        "Перезвоните мне, меня зовут Анна, +7 999 123-45-67",
        skill,
    )
    assert turn is not None
    assert turn.mode == "direct"
    assert turn.step == "send"
    assert turn.action is not None
    assert turn.slots.name == "Анна"


def test_info_and_contact_in_one_message_keeps_rag_answer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    turn = _plan(
        store,
        tenant.id,
        "sess-both-1",
        "Как связаться? Мой телефон +7 999 123-45-67",
        skill,
    )
    assert turn is not None
    assert turn.mode == "offer"
    assert turn.action is not None
    assert turn.answer == ""
    assert turn.answer_suffix == "Наш специалист свяжется с вами в ближайшее время."


@pytest.mark.parametrize(
    "question",
    [
        "Свяжитесь с врачом",
        "Как связаться с врачом?",
        "Можно ли связаться с врачом?",
        "Куда обратиться за юридической помощью?",
        "Мой телефон сломался",
        "Я оставил заявку на сайте",
    ],
)
def test_non_contact_questions_do_not_start_workflow(tmp_path: Path, question: str) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    assert _plan(store, tenant.id, "sess-negative", question, skill) is None
    assert store.active_skill_run(tenant.id, "sess-negative") is None


def test_cancel_closes_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-cancel"
    _plan(store, tenant.id, session, "Перезвоните мне", skill)
    turn = _plan(store, tenant.id, session, "Отмена", skill)
    assert turn is not None
    assert turn.step == "cancelled"
    assert store.active_skill_run(tenant.id, session) is None


def test_ambiguous_username_then_max(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-handle"
    _plan(store, tenant.id, session, "Как с вами связаться?", skill)

    ambiguous = _plan(store, tenant.id, session, "@anna", skill)
    assert ambiguous is not None
    assert "Telegram или MAX" in ambiguous.answer

    resolved = _plan(store, tenant.id, session, "макс", skill)
    assert resolved is not None
    assert resolved.step == "send"
    assert resolved.slots.max == "@anna"


def test_require_confirm_mode(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    store.update_skill(tenant.id, skill.id, {"config": {**skill.config, "require_confirm": True}})
    skill = store.get_skill(tenant.id, skill.id)
    session = "sess-confirm"

    first = _plan(store, tenant.id, session, "Перезвоните мне +7 999 123-45-67", skill)
    assert first is not None
    assert first.step == "confirm"
    assert first.action is None
    assert "Передать обращение" in first.answer

    second = _plan(store, tenant.id, session, "да", skill)
    assert second is not None
    assert second.step == "send"
    assert second.action is not None


def test_name_only_asks_for_channel(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-name"
    _plan(store, tenant.id, session, "Перезвоните мне", skill)
    turn = _plan(store, tenant.id, session, "Меня зовут Анна", skill)
    assert turn is not None
    assert turn.step == "collect_contacts"
    assert turn.slots.name == "Анна"
    assert not turn.slots.has_contact


def test_await_contact_does_not_repeat_offer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-no-repeat"
    _plan(store, tenant.id, session, "Как с вами связаться?", skill)
    assert _plan(store, tenant.id, session, "Спасибо", skill) is None
    assert store.active_skill_run(tenant.id, session) is not None


def test_expired_run_is_cancelled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-ttl"
    _plan(store, tenant.id, session, "Перезвоните мне", skill)
    run = store.active_skill_run(tenant.id, session)
    assert run is not None

    old = (datetime.now(UTC) - timedelta(minutes=120)).isoformat()
    with sqlite3.connect(tmp_path / "platform.db") as conn:
        conn.execute("UPDATE skill_runs SET updated_at = ? WHERE id = ?", (old, run.id))

    turn = _plan(store, tenant.id, session, "Как с вами связаться?", skill)
    assert turn is not None
    assert turn.step == "await_contact"
    assert store.active_skill_run(tenant.id, session).id != run.id  # type: ignore[union-attr]


def test_skill_name_constant() -> None:
    assert CONTACT_REQUEST_NAME == "contact-request"


def test_info_with_ambiguous_handle_keeps_offer_mode(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    turn = _plan(store, tenant.id, "sess-info-handle", "Как связаться? @anna", skill)
    assert turn is not None
    assert turn.mode == "offer"
    assert turn.answer == ""
    assert "Telegram или MAX" in turn.answer_suffix


def test_bare_no_cancels_active_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = _tenant(store)
    skill = _skill(store, tenant.id)
    session = "sess-no"
    _plan(store, tenant.id, session, "Перезвоните мне", skill)
    turn = _plan(store, tenant.id, session, "нет", skill)
    assert turn is not None
    assert turn.step == "cancelled"
    assert store.active_skill_run(tenant.id, session) is None
