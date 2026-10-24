"""Registration and the operator console on admin.spootnik.ru."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import (
    drop_tenant_pipeline,
    get_admin_auth,
    get_platform_store,
    platform_db_path,
)
from apps.api.app.deps import _data_root
from packages.integrations.gigachat.client import GigaChatApiError, get_gigachat_client
from packages.platform.anomalies import anomaly_reasons
from packages.platform.context import scope_var
from packages.platform.host import RequestScope
from packages.platform.layout import layout_for
from packages.platform.store import (
    EmailTakenError,
    InvalidSlugError,
    PlatformError,
    PlatformStore,
    SlugTakenError,
    TenantNotFoundError,
)
from packages.rag.prompts import PROJECT_ROOT
from packages.security.admin_auth import AdminAuthNotConfiguredError, AdminAuthService, TooManyAttemptsError

router = APIRouter(prefix="/v1/platform", tags=["platform"])
COOKIE_PATH = "/v1/platform"
_OPERATOR_HOSTS = {"platform_admin", "legacy", "landing"}


class RegisterRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=32)
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=256)
    widget_title: str = Field(default="", max_length=120)
    widget_description: str = Field(default="", max_length=150)


class LoginRequest(BaseModel):
    username: str
    password: str


class EnabledRequest(BaseModel):
    enabled: bool


def _scope() -> RequestScope:
    return scope_var.get()


def _operator_host() -> None:
    scope = _scope()
    on_help = scope.kind == "tenant" and scope.slug == "help"
    if scope.kind not in _OPERATOR_HOSTS and not on_help:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Консоль платформы открывается на help." + settings.platform_base_domain,
        )


def require_operator(
    auth: AdminAuthService = Depends(get_admin_auth),
    token: str | None = Cookie(default=None, alias=settings.admin_cookie_name),
) -> None:
    _operator_host()
    if not auth.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Задайте ADMIN_USERNAME и ADMIN_PASSWORD")
    if not auth.validate(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход оператора")


def _default_prompt() -> Path:
    return PROJECT_ROOT / "config" / "default_system_prompt.md"


def _provision(slug: str, prompt_source: Path | None = None) -> str:
    layout = layout_for(Path(_data_root()), slug)
    layout.ensure()
    source = prompt_source or _default_prompt()
    if not layout.prompt_file.exists() and source.exists():
        shutil.copyfile(source, layout.prompt_file)
    return str(layout.prompt_file)


@router.post("/register")
def register(payload: RegisterRequest, store: PlatformStore = Depends(get_platform_store)) -> dict:
    if _scope().kind not in {"landing", "legacy"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка отправляется с spootnik.ru")
    try:
        tenant = store.register(
            slug=payload.slug,
            name=payload.name,
            email=payload.email,
            password=payload.password,
            widget_title=payload.widget_title,
            widget_description=payload.widget_description,
        )
    except (InvalidSlugError, SlugTakenError, EmailTakenError, PlatformError) as exc:
        code = status.HTTP_409_CONFLICT if isinstance(exc, (SlugTakenError, EmailTakenError)) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    return {"status": tenant.status, "slug": tenant.slug}


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    auth: AdminAuthService = Depends(get_admin_auth),
) -> dict:
    _operator_host()
    try:
        token = auth.login(payload.username, payload.password, "platform")
    except AdminAuthNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except TooManyAttemptsError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=auth.session_ttl_seconds,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )
    return {"username": payload.username}


@router.get("/tenants", dependencies=[Depends(require_operator)])
def list_tenants(store: PlatformStore = Depends(get_platform_store)) -> dict:
    today = datetime.now(UTC).date()
    week_start = today - timedelta(days=6)
    previous_start = today - timedelta(days=13)
    items = []
    for tenant in store.list_tenants():
        layout = layout_for(Path(_data_root()), tenant.slug)
        days = store.usage_between(tenant.id, previous_start, today)
        today_row = next((day for day in days if day.day == today.isoformat()), None)
        previous = [day for day in days if day.day < today.isoformat() and day.day >= week_start.isoformat()]
        # Previous window for the spike is the 7 days before today when present.
        before_today = [day for day in days if day.day < today.isoformat()][-7:]
        disk = layout.disk_bytes()
        week = [day for day in days if day.day >= week_start.isoformat()]
        questions = sum(day.questions for day in week)
        tokens = sum(day.tokens_in + day.tokens_out for day in week)
        items.append(
            {
                "id": tenant.id,
                "slug": tenant.slug,
                "name": tenant.name,
                "email": tenant.email,
                "status": tenant.status,
                "documents": layout.document_count(),
                "disk_bytes": disk,
                "questions_7d": questions,
                "tokens_7d": tokens,
                "questions_today": today_row.questions if today_row else 0,
                "tokens_today": (today_row.tokens_in + today_row.tokens_out) if today_row else 0,
                "errors_today": today_row.errors if today_row else 0,
                "anomalies": anomaly_reasons(today_row, before_today, disk),
            }
        )
    return {"items": items}


@router.post("/tenants/{tenant_id}/approve", dependencies=[Depends(require_operator)])
def approve(tenant_id: int, store: PlatformStore = Depends(get_platform_store)) -> dict:
    tenant = store.get(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    path = _provision(tenant.slug)
    try:
        updated = store.approve(tenant_id, path)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"status": updated.status, "slug": updated.slug}


@router.post("/tenants/{tenant_id}/reject", dependencies=[Depends(require_operator)])
def reject(tenant_id: int, store: PlatformStore = Depends(get_platform_store)) -> dict:
    try:
        updated = store.reject(tenant_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"status": updated.status}


@router.post("/tenants/{tenant_id}/enabled", dependencies=[Depends(require_operator)])
def set_enabled(
    tenant_id: int,
    payload: EnabledRequest,
    store: PlatformStore = Depends(get_platform_store),
) -> dict:
    try:
        updated = store.set_enabled(tenant_id, payload.enabled)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    drop_tenant_pipeline(updated.id)
    return {"status": updated.status}


@router.get("/gigachat", dependencies=[Depends(require_operator)])
def gigachat_card(store: PlatformStore = Depends(get_platform_store)) -> dict:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=6)
    questions = tokens = errors_402 = recent_429 = 0
    for tenant in store.list_tenants():
        for day in store.usage_between(tenant.id, start, today):
            questions += day.questions
            tokens += day.tokens_in + day.tokens_out
            if day.day == today.isoformat():
                errors_402 += day.http_402
                recent_429 += day.http_429
    balance: dict | None = None
    balance_note = ""
    try:
        balance = get_gigachat_client().balance()
    except Exception as exc:
        text = str(exc)
        if "403" in text:
            balance_note = "Остаток кабинета по API недоступен для этого ключа"
        else:
            balance_note = "Остаток кабинета по API сейчас недоступен"
    alerts: list[str] = []
    if recent_429:
        alerts.append("За сегодня были отказы из-за одновременных запросов (429)")
    if errors_402:
        alerts.append("За сегодня модель ответила, что токены закончились (402)")
    return {
        "model": os.getenv("GIGACHAT_MODEL", ""),
        "scope": os.getenv("GIGACHAT_SCOPE", ""),
        "questions_7d": questions,
        "tokens_7d": tokens,
        "balance": balance,
        "balance_note": balance_note,
        "alerts": alerts,
        "database": str(platform_db_path()),
    }
