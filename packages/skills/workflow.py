"""Deterministic state machine for the ``contact-request`` workflow skill.

The LLM never decides whether to send a letter. It only summarises the request
later, inside the ``send_contact_request`` tool. This module is pure planning:
it reads/writes ``skill_runs`` and returns a :class:`WorkflowTurn` that the chat
route executes.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.platform.store import PlatformStore, SkillRun
from packages.skills.contacts import (
    ContactSlots,
    extract_contacts,
    is_cancel,
    is_confirm,
    merge_contacts,
    resolve_handle,
)
from packages.skills.models import Skill

CONTACT_REQUEST_NAME = "contact-request"
CONFIRMATION_TEXT = "Наш специалист свяжется с вами в ближайшее время."
OFFER_TEXT = (
    "Если удобнее, оставьте свой телефон, email, MAX или Telegram — передам специалисту."
)
ASK_CONTACT_TEXT = "Как с вами связаться? Телефон, email, MAX или Telegram."
AMBIGUOUS_HANDLE_TEXT = "Это Telegram или MAX? Уточните, пожалуйста."
CANCEL_TEXT = "Хорошо, ничего не отправляю."

_CONTACT_VERB = r"(?:связ|свяж|перезвон|позвон)\w*"
_CONTACT_SELF = r"(?:со\s+мной|мне|с\s+нами|нас)"

CONTACT_REQUEST_INTENT = re.compile(
    rf"(?:"
    rf"{_CONTACT_VERB}\s+{_CONTACT_SELF}|"
    rf"{_CONTACT_SELF}\s+{_CONTACT_VERB}|"
    r"(?:напиш)\w*\s+(?:мне|нам)|"
    r"(?:остав|переда|отправ)\w*\s+(?:мои\s+|свои\s+|свой\s+)?контакт|"
    r"(?:хочу|хотел[аи]?)\s+(?:оставить\s+)?(?:заявку|обращение)|"
    r"предложени\w*\s+о\s+сотрудничеств|"
    r"предлага\w*\s+сотрудничеств|"
    r"хотим\s+сотрудничать|"
    r"(?:хотим|хотел[аи]?)\s+предложить\s+(?:вам|фонду|сотрудничеств|проект|партн)|"
    r"(?:от|со)\s+(?:лица|стороны)\s+(?:компании|организации|фонда)|"
    r"коммерческое\s+предложение|"
    r"партн[её]рств"
    r")",
    re.IGNORECASE,
)

CONTACT_INFO_INTENT = re.compile(
    r"(?:"
    r"как\s+(?:с\s+вами|вас)\s+(?:связаться|созвониться|написать|позвонить)|"
    r"можно\s+ли\s+(?:с\s+вами\s+)?(?:связаться|созвониться|написать)(?!\s+с\s+(?:врач|доктор|клиник|специалист))|"
    r"как\s+связаться(?!\s+с\s+(?:врач|доктор|клиник|специалист))|"
    r"как\s+связаться\s+с\s+(?:фондом|организацией|вами)|"
    r"куда\s+(?:написать|позвонить)|"
    r"(?:ваши\s+контакты|контакты\s+(?:фонда|организации))|"
    r"есть\s+ли\s+(?:у\s+вас\s+)?(?:телефон|почта|email|telegram|макс)|"
    r"как\s+вас\s+найти"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PendingAction:
    tool: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class WorkflowTurn:
    answer: str
    step: str
    slots: ContactSlots
    run_id: int | None = None
    action: PendingAction | None = None
    finished: bool = False
    mode: str = "direct"  # direct | offer
    answer_suffix: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def contact_request_enabled() -> bool:
    return os.getenv("CONTACT_REQUEST_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def find_contact_skill(store: PlatformStore, tenant_id: int | None) -> Skill | None:
    if tenant_id is None or not contact_request_enabled():
        return None
    if os.getenv("SKILLS_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
        return None
    for skill in store.list_skills(tenant_id, enabled_only=True):
        if skill.skill_type == "workflow" and skill.name == CONTACT_REQUEST_NAME:
            return skill
    return None


def _cfg(skill: Skill, key: str, default: Any = None) -> Any:
    config = skill.config if isinstance(skill.config, dict) else {}
    return config.get(key, default)


def _text(skill: Skill, key: str, default: str) -> str:
    value = _cfg(skill, key, default)
    return str(value).strip() if value else default


def _expired(run: SkillRun, ttl_minutes: int) -> bool:
    try:
        updated = datetime.fromisoformat(run.updated_at)
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated > timedelta(minutes=ttl_minutes)


def _save(
    store: PlatformStore,
    tenant_id: int,
    run: SkillRun,
    *,
    step: str | None = None,
    slots: ContactSlots | None = None,
    action: dict[str, Any] | None = None,
) -> SkillRun:
    return store.save_skill_run(
        tenant_id,
        run.id,
        step=step,
        slots=slots.to_dict() if slots is not None else None,
        action=action,
    )


def _format_contacts(slots: ContactSlots) -> str:
    lines: list[str] = []
    if slots.name:
        lines.append(f"Имя: {slots.name}")
    if slots.organization:
        lines.append(f"Организация: {slots.organization}")
    if slots.phone:
        lines.append(f"Телефон: {slots.phone}")
    if slots.email:
        lines.append(f"Email: {slots.email}")
    if slots.telegram:
        lines.append(f"Telegram: {slots.telegram}")
    if slots.max:
        lines.append(f"MAX: {slots.max}")
    return "\n".join(lines)


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _send_turn(
    *,
    store: PlatformStore,
    tenant_id: int,
    skill: Skill,
    slots: ContactSlots,
    run: SkillRun,
    mode: str,
) -> WorkflowTurn:
    store.save_skill_run(tenant_id, run.id, step="send", slots=slots.to_dict())
    confirmation = _text(skill, "confirmation_text", CONFIRMATION_TEXT)
    request_id = _request_id()
    payload = {
        "request_id": request_id,
        "run_id": run.id,
        "slots": slots.to_dict(),
        "confirmation": confirmation,
    }
    return WorkflowTurn(
        answer="" if mode == "offer" else confirmation,
        step="send",
        slots=slots,
        run_id=run.id,
        action=PendingAction(tool="send_contact_request", payload=payload),
        mode=mode,
        answer_suffix=confirmation if mode == "offer" else "",
        extra={"request_id": request_id},
    )


def _ask_turn(
    *,
    skill: Skill,
    slots: ContactSlots,
    run: SkillRun,
    step: str = "collect_contacts",
) -> WorkflowTurn:
    return WorkflowTurn(
        answer=_text(skill, "ask_contact_text", ASK_CONTACT_TEXT),
        step=step,
        slots=slots,
        run_id=run.id,
    )


def _ambiguous_turn(
    *,
    skill: Skill,
    slots: ContactSlots,
    run: SkillRun,
    step: str,
) -> WorkflowTurn:
    return WorkflowTurn(
        answer=_text(skill, "ambiguous_handle_question", AMBIGUOUS_HANDLE_TEXT),
        step=step,
        slots=slots,
        run_id=run.id,
    )


def _cancel_turn(
    *,
    store: PlatformStore,
    tenant_id: int,
    run: SkillRun,
    slots: ContactSlots,
    skill: Skill,
) -> WorkflowTurn:
    store.close_skill_run(tenant_id, run.id, status="cancelled")
    return WorkflowTurn(
        answer=_text(skill, "cancel_text", CANCEL_TEXT),
        step="cancelled",
        slots=slots,
        run_id=run.id,
        finished=True,
    )


def _offer_turn(
    *,
    store: PlatformStore,
    tenant_id: int,
    skill: Skill,
    slots: ContactSlots,
    run: SkillRun,
    action: dict[str, Any] | None = None,
    suffix: str | None = None,
) -> WorkflowTurn:
    offer = suffix or _text(skill, "offer_text", OFFER_TEXT)
    store.save_skill_run(
        tenant_id,
        run.id,
        step="await_contact",
        slots=slots.to_dict(),
        action={**(action or {}), "offer_sent": True},
    )
    return WorkflowTurn(
        answer="",
        step="await_contact",
        slots=slots,
        run_id=run.id,
        mode="offer",
        answer_suffix=offer,
    )


def _start(
    store: PlatformStore,
    tenant_id: int,
    session_id: str,
    skill: Skill,
    step: str,
    slots: ContactSlots,
) -> SkillRun:
    return store.start_skill_run(
        tenant_id,
        session_id,
        skill.id,
        step,
        slots.to_dict(),
    )


def _confirm_turn(
    *,
    store: PlatformStore,
    tenant_id: int,
    skill: Skill,
    slots: ContactSlots,
    run: SkillRun,
) -> WorkflowTurn:
    store.save_skill_run(tenant_id, run.id, step="confirm", slots=slots.to_dict())
    contacts = _format_contacts(slots)
    answer = (
        "Проверьте, пожалуйста, контакты:\n"
        f"{contacts}\n\n"
        "Передать обращение в фонд? Ответьте «да» или исправьте контакты."
    )
    return WorkflowTurn(answer=answer, step="confirm", slots=slots, run_id=run.id)


def _looks_like_contact_answer(question: str) -> bool:
    text = " ".join(question.strip().split())
    return len(text) <= 40 and "?" not in text


def plan_workflow_turn(
    *,
    store: PlatformStore,
    tenant_id: int | None,
    session_id: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    skill: Skill | None = None,
) -> WorkflowTurn | None:
    """Return the next workflow step, or ``None`` for the normal RAG path."""
    del history  # reserved for future aside handling
    if tenant_id is None:
        return None
    if skill is None:
        skill = find_contact_skill(store, tenant_id)
    if skill is None:
        return None

    config = skill.config if isinstance(skill.config, dict) else {}
    ttl_minutes = int(config.get("await_contact_ttl_minutes") or 30)
    require_confirm = bool(config.get("require_confirm", False))
    cancel_phrases = config.get("cancel_phrases") if isinstance(config.get("cancel_phrases"), list) else None
    confirm_phrases = config.get("confirm_phrases") if isinstance(config.get("confirm_phrases"), list) else None
    question = (question or "").strip()
    lowered = question.casefold()
    request_intent = bool(CONTACT_REQUEST_INTENT.search(question))
    info_intent = bool(CONTACT_INFO_INTENT.search(question))
    new_slots = extract_contacts(question)

    run = store.active_skill_run(tenant_id, session_id)
    if run is not None and run.skill_id != skill.id:
        return None
    if run is not None and _expired(run, ttl_minutes):
        store.close_skill_run(tenant_id, run.id, status="cancelled")
        run = None

    if run is not None:
        slots = ContactSlots.from_dict(run.slots)
        slots = merge_contacts(slots, new_slots)
        slots = resolve_handle(slots, question)

        if is_cancel(question, cancel_phrases):
            return _cancel_turn(
                store=store, tenant_id=tenant_id, run=run, slots=slots, skill=skill
            )

        if run.step == "await_contact":
            if slots.has_contact:
                return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run, mode="direct")
            if slots.handle:
                _save(store, tenant_id, run, slots=slots)
                return _ambiguous_turn(skill=skill, slots=slots, run=run, step="await_contact")
            if info_intent:
                return _offer_turn(
                    store=store,
                    tenant_id=tenant_id,
                    skill=skill,
                    slots=slots,
                    run=run,
                    action=run.action,
                )
            if request_intent:
                _save(store, tenant_id, run, slots=slots)
                return _ask_turn(skill=skill, slots=slots, run=run, step="await_contact")
            if new_slots.name or new_slots.organization:
                _save(store, tenant_id, run, slots=slots)
                return _ask_turn(skill=skill, slots=slots, run=run, step="await_contact")
            _save(store, tenant_id, run, slots=slots)
            return None

        if run.step == "collect_contacts":
            if slots.has_contact:
                if require_confirm:
                    return _confirm_turn(
                        store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run
                    )
                return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run, mode="direct")
            if slots.handle:
                _save(store, tenant_id, run, slots=slots)
                return _ambiguous_turn(
                    skill=skill, slots=slots, run=run, step="collect_contacts"
                )
            if info_intent:
                return _offer_turn(
                    store=store,
                    tenant_id=tenant_id,
                    skill=skill,
                    slots=slots,
                    run=run,
                )
            if new_slots.name or new_slots.organization or _looks_like_contact_answer(question):
                _save(store, tenant_id, run, slots=slots)
                return _ask_turn(skill=skill, slots=slots, run=run)
            store.close_skill_run(tenant_id, run.id, status="cancelled")
            return None

        if run.step == "confirm":
            if is_confirm(question, confirm_phrases):
                return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run, mode="direct")
            if new_slots.has_contact:
                return _confirm_turn(
                    store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run
                )
            if "?" in question:
                store.close_skill_run(tenant_id, run.id, status="cancelled")
                return None
            _save(store, tenant_id, run, slots=slots)
            return _confirm_turn(
                store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run
            )

        if run.step == "send":
            if slots.has_contact:
                return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=slots, run=run, mode="direct")
            store.close_skill_run(tenant_id, run.id, status="failed")
            return None

    # No active run: decide whether this message starts a scenario.
    if not request_intent and not info_intent:
        return None

    if info_intent and not request_intent:
        if new_slots.has_contact:
            if require_confirm:
                run = _start(store, tenant_id, session_id, skill, "confirm", new_slots)
                confirmed = _confirm_turn(
                    store=store, tenant_id=tenant_id, skill=skill, slots=new_slots, run=run
                )
                return replace(confirmed, mode="offer", answer="", answer_suffix=confirmed.answer)
            run = _start(store, tenant_id, session_id, skill, "send", new_slots)
            return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=new_slots, run=run, mode="offer")
        if new_slots.handle:
            run = _start(store, tenant_id, session_id, skill, "await_contact", new_slots)
            turn = _ambiguous_turn(
                skill=skill, slots=new_slots, run=run, step="await_contact"
            )
            return replace(turn, mode="offer", answer="", answer_suffix=turn.answer)
        run = _start(store, tenant_id, session_id, skill, "await_contact", new_slots)
        return _offer_turn(
            store=store,
            tenant_id=tenant_id,
            skill=skill,
            slots=new_slots,
            run=run,
        )

    # Explicit contact request.
    if new_slots.has_contact:
        if require_confirm:
            run = _start(store, tenant_id, session_id, skill, "confirm", new_slots)
            confirmed = _confirm_turn(
                store=store, tenant_id=tenant_id, skill=skill, slots=new_slots, run=run
            )
            if info_intent:
                return replace(confirmed, mode="offer", answer="", answer_suffix=confirmed.answer)
            return confirmed
        run = _start(store, tenant_id, session_id, skill, "send", new_slots)
        mode = "offer" if info_intent else "direct"
        return _send_turn(store=store, tenant_id=tenant_id, skill=skill, slots=new_slots, run=run, mode=mode)
    if new_slots.handle:
        run = _start(store, tenant_id, session_id, skill, "collect_contacts", new_slots)
        turn = _ambiguous_turn(skill=skill, slots=new_slots, run=run, step="collect_contacts")
        if info_intent:
            return replace(turn, mode="offer", answer="", answer_suffix=turn.answer)
        return turn
    run = _start(store, tenant_id, session_id, skill, "collect_contacts", new_slots)
    if info_intent:
        return _offer_turn(
            store=store,
            tenant_id=tenant_id,
            skill=skill,
            slots=new_slots,
            run=run,
            suffix=_text(skill, "ask_contact_text", ASK_CONTACT_TEXT),
        )
    return _ask_turn(skill=skill, slots=new_slots, run=run)
