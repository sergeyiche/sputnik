"""Single-admin authentication with in-memory sessions and login throttling.

Sessions live in process memory: an API restart logs the admin out, which is
acceptable for a single administrator and avoids extra infrastructure.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class AdminAuthNotConfiguredError(RuntimeError):
    pass


class TooManyAttemptsError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Слишком много попыток входа. Повторите через {retry_after} с.")
        self.retry_after = retry_after


@dataclass
class _Attempts:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class AdminAuthService:
    def __init__(
        self,
        username: str,
        password: str,
        session_ttl_seconds: int = 8 * 3600,
        max_failures: int = 5,
        failure_window_seconds: int = 15 * 60,
        lockout_seconds: int = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._username = username
        self._password = password
        self._ttl = session_ttl_seconds
        self._max_failures = max_failures
        self._window = failure_window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._sessions: dict[str, float] = {}
        self._attempts: dict[str, _Attempts] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._username and self._password)

    @property
    def session_ttl_seconds(self) -> int:
        return self._ttl

    def login(self, username: str, password: str, client_id: str) -> str | None:
        """Return a new session token, ``None`` for wrong credentials."""
        if not self.configured:
            raise AdminAuthNotConfiguredError("Админка не настроена: задайте ADMIN_USERNAME и ADMIN_PASSWORD")

        now = self._clock()
        with self._lock:
            attempts = self._attempts.setdefault(client_id, _Attempts())
            if attempts.locked_until > now:
                raise TooManyAttemptsError(int(attempts.locked_until - now) + 1)

        valid = hmac.compare_digest(username.encode(), self._username.encode()) & hmac.compare_digest(
            password.encode(), self._password.encode()
        )

        with self._lock:
            attempts = self._attempts.setdefault(client_id, _Attempts())
            if not valid:
                attempts.failures = [t for t in attempts.failures if now - t < self._window] + [now]
                if len(attempts.failures) >= self._max_failures:
                    attempts.locked_until = now + self._lockout
                    attempts.failures.clear()
                return None

            self._attempts.pop(client_id, None)
            self._purge_expired(now)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + self._ttl
            return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        now = self._clock()
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if expires <= now:
                self._sessions.pop(token, None)
                return False
            return True

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def _purge_expired(self, now: float) -> None:
        for token in [t for t, exp in self._sessions.items() if exp <= now]:
            self._sessions.pop(token, None)
