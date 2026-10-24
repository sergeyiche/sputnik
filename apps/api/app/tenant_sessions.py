"""In-memory sessions for an organization owner. Cookie is host-only."""

from __future__ import annotations

import secrets
import time

_sessions: dict[str, tuple[int, float]] = {}


def issue(tenant_id: int, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = (tenant_id, time.time() + ttl_seconds)
    return token


def tenant_id_for(token: str | None) -> int | None:
    if not token:
        return None
    row = _sessions.get(token)
    if row is None:
        return None
    tenant_id, expires = row
    if expires < time.time():
        _sessions.pop(token, None)
        return None
    return tenant_id


def drop(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)


def drop_for_tenant(tenant_id: int) -> None:
    stale = [token for token, (owner_id, _) in _sessions.items() if owner_id == tenant_id]
    for token in stale:
        _sessions.pop(token, None)
