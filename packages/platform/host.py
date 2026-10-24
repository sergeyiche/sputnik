"""Resolve the request host into a landing, platform admin, tenant or legacy scope."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ScopeKind = Literal["landing", "platform_admin", "tenant", "legacy", "unknown"]

RESERVED_SLUGS = frozenset(
    {"www", "api", "admin", "app", "mail", "platform", "static", "widget", "help"}
)
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
_LEGACY_HOSTS = frozenset({"localhost", "127.0.0.1", "testclient", "testserver", "0.0.0.0"})


@dataclass(frozen=True)
class RequestScope:
    kind: ScopeKind
    host: str
    slug: str | None = None


def normalize_host(value: str) -> str:
    host = value.split(",")[0].strip().lower()
    if host.startswith("["):
        return host
    return host.split(":")[0].strip(".")


def valid_slug(slug: str) -> bool:
    return bool(_SLUG.fullmatch(slug))


def slug_available(slug: str) -> bool:
    return valid_slug(slug) and slug not in RESERVED_SLUGS


def resolve_host(raw_host: str, base_domain: str) -> RequestScope:
    host = normalize_host(raw_host)
    base = base_domain.strip().lower().strip(".")
    if host in _LEGACY_HOSTS or host == "":
        return RequestScope(kind="legacy", host=host)
    if host == base or host == f"www.{base}":
        return RequestScope(kind="landing", host=host)
    if host == f"admin.{base}":
        return RequestScope(kind="platform_admin", host=host)
    tenant = _subdomain_slug(host, base)
    if tenant is None and host.endswith(".localhost"):
        tenant = host[: -len(".localhost")]
    if tenant and valid_slug(tenant) and "." not in tenant:
        return RequestScope(kind="tenant", host=host, slug=tenant)
    return RequestScope(kind="unknown", host=host)


def _subdomain_slug(host: str, base: str) -> str | None:
    suffix = f".{base}"
    if not base or not host.endswith(suffix):
        return None
    return host[: -len(suffix)]
