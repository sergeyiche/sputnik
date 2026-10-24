"""Bind Host to a platform scope for the duration of the request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.api.app.config import settings
from apps.api.app.deps import get_platform_store
from packages.platform.context import scope_var, tenant_var
from packages.platform.host import resolve_host


class PlatformHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        scope = resolve_host(request.headers.get("host", ""), settings.platform_base_domain)
        scope_token = scope_var.set(scope)
        tenant_token = None
        if scope.kind == "tenant" and scope.slug:
            tenant_token = tenant_var.set(get_platform_store().get_by_slug(scope.slug))
        try:
            return await call_next(request)
        finally:
            if tenant_token is not None:
                tenant_var.reset(tenant_token)
            scope_var.reset(scope_token)
