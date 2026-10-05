"""Unit tests for admin authentication."""

from __future__ import annotations

import pytest

from packages.security.admin_auth import (
    AdminAuthNotConfiguredError,
    AdminAuthService,
    TooManyAttemptsError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def auth(clock: FakeClock) -> AdminAuthService:
    return AdminAuthService("admin", "secret", session_ttl_seconds=60, max_failures=3, lockout_seconds=120, clock=clock)


def test_login_and_validate(auth: AdminAuthService) -> None:
    token = auth.login("admin", "secret", "1.1.1.1")
    assert token and auth.validate(token)


def test_wrong_password_returns_none(auth: AdminAuthService) -> None:
    assert auth.login("admin", "nope", "1.1.1.1") is None


def test_session_expires(auth: AdminAuthService, clock: FakeClock) -> None:
    token = auth.login("admin", "secret", "1.1.1.1")
    clock.now += 61
    assert not auth.validate(token)


def test_logout_invalidates(auth: AdminAuthService) -> None:
    token = auth.login("admin", "secret", "1.1.1.1")
    auth.logout(token)
    assert not auth.validate(token)


def test_lockout_after_failures(auth: AdminAuthService, clock: FakeClock) -> None:
    for _ in range(3):
        auth.login("admin", "bad", "2.2.2.2")
    with pytest.raises(TooManyAttemptsError):
        auth.login("admin", "secret", "2.2.2.2")
    assert auth.login("admin", "secret", "3.3.3.3") is not None

    clock.now += 121
    assert auth.login("admin", "secret", "2.2.2.2") is not None


def test_not_configured() -> None:
    with pytest.raises(AdminAuthNotConfiguredError):
        AdminAuthService("", "").login("admin", "x", "1.1.1.1")
