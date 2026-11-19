"""Deterministic contact extraction for the contact-request workflow."""

from __future__ import annotations

import pytest

from packages.skills.contacts import (
    ContactSlots,
    classify_kind,
    extract_contacts,
    is_cancel,
    is_confirm,
    merge_contacts,
    normalize_phone,
    resolve_handle,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("+7 999 123 45 67", "+79991234567"),
        ("7-999-123-45-67", "+79991234567"),
        ("8 999 123 45 67", "+79991234567"),
        ("103", None),
        ("112", None),
        ("12345", None),
    ],
)
def test_normalize_phone(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


def test_extract_full_patient_contact() -> None:
    slots = extract_contacts("Перезвоните мне, меня зовут Анна, +7 999 123-45-67")
    assert slots.name == "Анна"
    assert slots.phone == "+79991234567"
    assert slots.has_contact
    assert slots.contact_channel == "phone"


def test_extract_email() -> None:
    slots = extract_contacts("Пишите на anna@example.org")
    assert slots.email == "anna@example.org"
    assert not slots.telegram
    assert slots.contact_channel == "email"


def test_extract_telegram_and_max() -> None:
    telegram = extract_contacts("Мой телеграм @anna_parkinson")
    assert telegram.telegram == "@anna_parkinson"
    assert telegram.contact_channel == "telegram"

    messenger = extract_contacts("Пишите в MAX: @anna")
    assert messenger.max == "@anna"
    assert messenger.contact_channel == "max"

    link = extract_contacts("Мой MAX: max.ru/anna")
    assert link.max == "@anna"


def test_bare_username_is_ambiguous() -> None:
    slots = extract_contacts("@anna")
    assert slots.handle == "@anna"
    assert not slots.has_contact
    assert slots.contact_channel == ""

    assert resolve_handle(slots, "телеграм").telegram == "@anna"
    assert resolve_handle(slots, "макс").max == "@anna"


def test_organization_and_kind() -> None:
    slots = extract_contacts("ООО «Ромашка» предлагает фонду совместный проект. Иван, ivan@example.org")
    assert slots.organization == "ООО «Ромашка»"
    assert slots.kind == "partnership"
    assert slots.email == "ivan@example.org"

    caregiver = extract_contacts("Мужу поставили диагноз, хотим поговорить с координатором, 8 999 123-45-67")
    assert caregiver.kind == "caregiver"

    assert classify_kind("предложение о сотрудничестве") == "partnership"


def test_name_or_organization_alone_is_not_a_contact() -> None:
    slots = extract_contacts("Меня зовут Анна, я из клиники")
    assert slots.name == "Анна"
    assert not slots.has_contact
    assert slots.contact_channel == ""


def test_merge_keeps_old_values_and_resolves_handle() -> None:
    base = extract_contacts("Меня зовут Анна, @anna")
    merged = merge_contacts(base, extract_contacts("телеграм"))
    assert merged.name == "Анна"
    assert merged.telegram == "@anna"
    assert merged.handle == ""

    phone = merge_contacts(extract_contacts("Перезвоните мне"), extract_contacts("+7 999 123-45-67"))
    assert phone.phone == "+79991234567"
    assert phone.has_contact


def test_cancel_and_confirm_phrases() -> None:
    assert is_cancel("Отмена")
    assert is_cancel("не отправляйте")
    assert not is_cancel("Не отправляйте это врачу")
    assert is_confirm("Да, отправляйте")
    assert is_confirm("подтверждаю")
    assert not is_confirm("Да, но сначала расскажите")


def test_contact_slots_roundtrip() -> None:
    slots = ContactSlots(name="Анна", phone="+79991234567", telegram="@anna")
    assert ContactSlots.from_dict(slots.to_dict()) == slots
