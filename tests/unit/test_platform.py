"""Host routing, registry and anomaly highlights."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from packages.platform.anomalies import anomaly_reasons
from packages.platform.emails import valid_email
from packages.platform.host import resolve_host, slug_available
from packages.platform.passwords import hash_password, verify_password
from packages.platform.phones import format_phone, normalize_phone
from packages.platform.store import (
    EmailTakenError,
    InvalidSlugError,
    PARKINSON_GREETING,
    PlatformError,
    SlugTakenError,
    PlatformStore,
    UsageDay,
    normalize_web_color,
)


def test_resolve_public_hosts() -> None:
    assert resolve_host("spootnik.ru", "spootnik.ru").kind == "landing"
    assert resolve_host("www.spootnik.ru:443", "spootnik.ru").kind == "landing"
    assert resolve_host("admin.spootnik.ru", "spootnik.ru").kind == "platform_admin"
    scope = resolve_host("help.spootnik.ru", "spootnik.ru")
    assert scope.kind == "tenant" and scope.slug == "help"
    assert resolve_host("testclient", "spootnik.ru").kind == "legacy"
    assert resolve_host("acme.localhost", "spootnik.ru").slug == "acme"
    assert resolve_host("evil.example", "spootnik.ru").kind == "unknown"


def test_reserved_slug_cannot_be_registered() -> None:
    assert slug_available("fond")
    assert not slug_available("help")
    assert not slug_available("Admin")


def test_register_approve_and_isolate_login(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    with pytest.raises(InvalidSlugError):
        store.register(slug="help", name="Спутник", email="a@b.c", password="secret123")

    pending = store.register(slug="acme", name="Фонд А", email="owner@acme.org", password="secret123")
    assert pending.status == "pending"
    assert store.authenticate("owner@acme.org", "secret123").status == "pending"
    assert store.authenticate("owner@acme.org", "nope") is None

    with pytest.raises(EmailTakenError):
        store.register(slug="other", name="Б", email="owner@acme.org", password="secret123")

    active = store.approve(pending.id, "/data/tenants/acme/prompt_default.md")
    assert active.status == "active"
    assert active.collection_name == "tenant_acme"
    settings = store.settings_for(active.id)
    assert settings.show_sources is True
    assert settings.disclaimer
    assert settings.title == "Спутник"
    assert settings.greeting == ""

    named = store.register(
        slug="named",
        name="Фонд Б",
        email="owner@named.org",
        password="secret123",
        widget_title="Навигатор",
        widget_description="Ответы по материалам фонда",
    )
    named_settings = store.settings_for(named.id)
    assert named_settings.title == "Навигатор"
    assert named_settings.description == "Ответы по материалам фонда"
    with pytest.raises(PlatformError):
        store.register(
            slug="long",
            name="Фонд В",
            email="owner@long.org",
            password="secret123",
            widget_description="я" * 151,
        )
    with pytest.raises(PlatformError):
        store.update_settings(
            named.id,
            title="Навигатор",
            description="Ответы по материалам фонда",
            primary_color="#fc7f03",
            show_sources=True,
            greeting="",
            disclaimer="ю" * 251,
            legal_notes="",
        )

    suspended = store.set_enabled(active.id, False)
    assert suspended.status == "suspended"
    assert store.set_enabled(active.id, True).status == "active"


def test_web_color_normalizes_to_six_digits() -> None:
    assert normalize_web_color("#FC7F03") == "#fc7f03"
    assert normalize_web_color("#abc") == "#aabbcc"
    with pytest.raises(PlatformError):
        normalize_web_color("orange")


def test_email_is_required(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    assert valid_email("owner@acme.org")
    assert not valid_email("help")
    assert not valid_email("")
    with pytest.raises(PlatformError):
        store.register(slug="acme", name="Фонд А", email="help", password="secret123")


def test_missing_email_is_filled(tmp_path: Path) -> None:
    path = tmp_path / "platform.db"
    store = PlatformStore(path)
    store.register(slug="acme", name="Фонд А", email="owner@acme.org", password="secret123")
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE users SET email = 'help'")
    conn.commit()
    conn.close()

    reopened = PlatformStore(path)
    tenant = reopened.get_by_slug("acme")
    assert tenant is not None
    assert tenant.email == "acme@spootnik.local"


def test_password_reset_is_single_use(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    tenant = store.register(slug="acme", name="Фонд А", email="owner@acme.org", password="secret123")
    other = store.register(slug="beta", name="Б", email="owner@beta.org", password="secret123")
    assert store.issue_password_reset("owner@beta.org", tenant_id=tenant.id, ttl_seconds=60) is None
    assert store.issue_password_reset("missing@acme.org", tenant_id=tenant.id, ttl_seconds=60) is None

    issued = store.issue_password_reset("owner@acme.org", tenant_id=tenant.id, ttl_seconds=60)
    assert issued is not None and issued.slug == "acme"
    again = store.issue_password_reset("owner@acme.org", tenant_id=tenant.id, ttl_seconds=60)
    assert again is not None
    with pytest.raises(PlatformError):
        store.reset_password(issued.token, "new-secret")

    updated = store.reset_password(again.token, "new-secret")
    assert updated.id == tenant.id
    assert store.authenticate("owner@acme.org", "secret123") is None
    assert store.authenticate("owner@acme.org", "new-secret") is not None
    with pytest.raises(PlatformError):
        store.reset_password(again.token, "another-one")
    assert other.email == "owner@beta.org"


def test_phone_keeps_plus_seven_and_ten_digits() -> None:
    assert normalize_phone("+7 (999) 000-11-22") == "+79990001122"
    assert normalize_phone("8 999 000 11 22") == "+79990001122"
    assert normalize_phone("79990001122") == "+79990001122"
    assert normalize_phone("+7 8 999 000-11-22") == "+79990001122"
    assert format_phone("+79990001122") == "+7 999 000-11-22"
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_parkinson_keeps_the_fund_greeting(tmp_path: Path) -> None:
    path = tmp_path / "platform.db"
    store = PlatformStore(path)
    store.register(slug="parkinson", name="Фонд", email="owner@parkinson.org", password="secret123")
    store.register(slug="acme", name="Другая", email="owner@acme.org", password="secret123")
    reopened = PlatformStore(path)
    parkinson = reopened.get_by_slug("parkinson")
    other = reopened.get_by_slug("acme")
    assert parkinson is not None and other is not None
    assert reopened.settings_for(parkinson.id).greeting == PARKINSON_GREETING
    assert reopened.settings_for(other.id).greeting == ""


def test_starter_questions_stay_on_that_tenant(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    acme = store.register(slug="acme", name="Фонд А", email="owner@acme.org", password="secret123")
    beta = store.register(slug="beta", name="Фонд Б", email="owner@beta.org", password="secret123")
    first = store.add_starter_question(acme.id, "Как к вам записаться?")
    store.add_starter_question(acme.id, "Какие документы нужны?")
    assert store.sample_starter_questions(beta.id, 3) == []
    sample = store.sample_starter_questions(acme.id, 3)
    assert set(sample) == {"Как к вам записаться?", "Какие документы нужны?"}
    store.update_starter_question(acme.id, first.id, "Как записаться на приём?")
    store.delete_starter_question(acme.id, first.id)
    assert [item.text for item in store.list_starter_questions(acme.id)] == ["Какие документы нужны?"]
    with pytest.raises(PlatformError):
        store.add_starter_question(acme.id, "Какие документы нужны?")


def test_signup_reserves_slug_and_creates_pending_tenant(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    issued = store.start_signup(name="Фонд А", phone="8 (999) 000-11-22", email="owner@acme.org", ttl_seconds=3600)
    assert store.list_tenants() == []
    with pytest.raises(EmailTakenError):
        store.start_signup(name="Другой", phone="+79990001123", email="owner@acme.org", ttl_seconds=3600)

    opened = store.open_signup(issued.token)
    assert opened.step == "organization"
    assert opened.phone == "+7 999 000-11-22"
    again = store.open_signup(issued.token)
    assert again.email == "owner@acme.org"

    saved = store.save_signup_organization(
        issued.token, slug="acme", password="secret123", phone="+7 900 100-00-00"
    )
    assert saved.step == "widget"
    assert saved.phone == "+7 900 100-00-00"
    other = store.start_signup(name="Фонд Б", phone="+79990001133", email="owner@beta.org", ttl_seconds=3600)
    with pytest.raises(SlugTakenError):
        store.save_signup_organization(other.token, slug="acme", password="secret123")

    pending = store.complete_signup(
        issued.token,
        widget_title="Навигатор",
        widget_description="Ответы по материалам",
        primary_color="#FC7F03",
        prompt_path="/data/tenants/acme/prompt_default.md",
    )
    assert pending.status == "pending"
    assert pending.phone == "+79001000000"
    assert store.authenticate("owner@acme.org", "secret123").id == pending.id
    settings = store.settings_for(pending.id)
    assert settings.title == "Навигатор"
    assert settings.primary_color == "#fc7f03"
    assert settings.show_sources is True
    assert store.open_signup(issued.token).step == "done"
    with pytest.raises(EmailTakenError):
        store.start_signup(name="Фонд А", phone="+79990001122", email="owner@acme.org", ttl_seconds=3600)

    expired = store.start_signup(name="Старая", phone="+79990001144", email="old@acme.org", ttl_seconds=-1)
    replacement = store.start_signup(name="Старая", phone="+79990001144", email="old@acme.org", ttl_seconds=3600)
    with pytest.raises(PlatformError):
        store.open_signup(expired.token)
    assert store.open_signup(replacement.token).step == "organization"


def test_password_roundtrip() -> None:
    stored = hash_password("secret123")
    assert verify_password("secret123", stored)
    assert not verify_password("secret124", stored)


def test_usage_and_anomaly(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    tenant = store.register(slug="acme", name="Фонд А", email="owner@acme.org", password="secret123")
    start = date(2026, 10, 1)
    for offset in range(7):
        store.record_usage(tenant.id, questions=2, tokens_in=100, tokens_out=50, day=date(2026, 10, offset + 1))
    store.record_usage(
        tenant.id,
        questions=30,
        tokens_in=1000,
        tokens_out=1000,
        empty_retrievals=20,
        errors=1,
        day=date(2026, 10, 9),
    )
    days = store.usage_between(tenant.id, start, date(2026, 10, 9))
    today = days[-1]
    reasons = anomaly_reasons(today, days[:-1], disk_bytes=10)
    assert "Всплеск вопросов или токенов" in reasons
    assert "Больше половины ответов без базы знаний" in reasons
    assert "Есть ошибки модели" in reasons
    assert anomaly_reasons(UsageDay("2026-10-09", 3, 10, 10, 0, 0, 0, 0), [], 10) == []
