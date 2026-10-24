"""Request-scoped organization. Empty on the legacy host (localhost, tests)."""

from __future__ import annotations

from contextvars import ContextVar

from packages.platform.host import RequestScope
from packages.platform.store import Tenant

scope_var: ContextVar[RequestScope] = ContextVar(
    "platform_scope",
    default=RequestScope(kind="legacy", host=""),
)
tenant_var: ContextVar[Tenant | None] = ContextVar("platform_tenant", default=None)
