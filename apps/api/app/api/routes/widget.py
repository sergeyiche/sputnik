"""Public widget appearance and the tenant admin that edits it."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from apps.api.app.api.routes.admin import require_admin
from apps.api.app.config import settings
from apps.api.app.deps import drop_tenant_pipeline, get_platform_store
from apps.api.app.deps import _data_root
from packages.platform.context import scope_var, tenant_var
from packages.platform.layout import layout_for
from packages.platform.store import (
    DEFAULT_WIDGET_POSITION,
    MAX_GREETING,
    WIDGET_POSITIONS,
    PlatformError,
    PlatformStore,
)

router = APIRouter(tags=["widget"])


class WidgetSettingsBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=150)
    primary_color: str = Field(default="#fc7f03", max_length=16)
    show_sources: bool = True
    greeting: str = Field(default="", max_length=MAX_GREETING)
    disclaimer: str = Field(..., min_length=1, max_length=250)
    legal_notes: str = Field(default="", max_length=3000)
    position: str = Field(default=DEFAULT_WIDGET_POSITION)


_CABINET = frozenset({"pending", "active", "suspended"})


def _present():
    """Any organization that already has a subdomain, including a pending one."""
    tenant = tenant_var.get()
    if scope_var.get().kind != "tenant" or tenant is None:
        raise HTTPException(404, "Организация не найдена")
    return tenant


def _cabinet():
    tenant = _present()
    if tenant.status not in _CABINET:
        raise HTTPException(404, "Организация не найдена или отклонена")
    return tenant


def _public(store: PlatformStore, tenant) -> dict:
    settings_row = store.settings_for(tenant.id)
    origin = f"https://{tenant.slug}.{settings.platform_base_domain}"
    position = settings_row.position if settings_row.position in WIDGET_POSITIONS else DEFAULT_WIDGET_POSITION
    return {
        "name": tenant.name,
        "status": tenant.status,
        "title": settings_row.title,
        "description": settings_row.description,
        "primary_color": settings_row.primary_color,
        "show_sources": settings_row.show_sources,
        "greeting": settings_row.greeting,
        "disclaimer": settings_row.disclaimer,
        "legal_notes": settings_row.legal_notes,
        "position": position,
        "logo_url": f"{origin}/v1/widget/logo" if settings_row.logo_name else "",
        "origin": origin,
        "snippet": (
            f'<link rel="stylesheet" href="{origin}/widget/sputnik.css" />\n'
            f'<link rel="stylesheet" href="{origin}/v1/widget/theme.css" />\n'
            f'<script src="{origin}/widget/sputnik.js" defer></script>\n'
            "<sputnik-widget></sputnik-widget>"
        ),
        "open_button_snippet": (
            '<button type="button" id="sputnik-open">Задать вопрос ассистенту</button>\n'
            "<script>\n"
            "  document.getElementById('sputnik-open').addEventListener('click', function () {\n"
            "    if (window.ParkinsonChatWidget && window.ParkinsonChatWidget.open) {\n"
            "      window.ParkinsonChatWidget.open();\n"
            "    } else {\n"
            "      window.dispatchEvent(new CustomEvent('sputnik-open'));\n"
            "    }\n"
            "  });\n"
            "</script>"
        ),
    }


def _position_css(position: str, gap: int) -> str:
    """CSS-правила для иконки виджета в выбранном углу."""
    if position == "top-right":
        return f"top: {gap}px; right: {gap}px; bottom: auto; left: auto;"
    if position == "top-left":
        return f"top: {gap}px; left: {gap}px; bottom: auto; right: auto;"
    if position == "bottom-left":
        return f"bottom: {gap}px; left: {gap}px; top: auto; right: auto;"
    return f"bottom: {gap}px; right: {gap}px; top: auto; left: auto;"


@router.get("/v1/widget/config")
def widget_config(store: PlatformStore = Depends(get_platform_store)) -> dict:
    return _public(store, _present())


@router.get("/v1/widget/theme.css")
def widget_theme(store: PlatformStore = Depends(get_platform_store)) -> Response:
    """Положение иконки виджета для конкретной организации.

    Файл формируется из настроек вкладки «Виджет»: после сохранения выбора
    угла в кабинете этот CSS меняется сразу, без правки кода на сайте клиента.
    """
    settings_row = store.settings_for(_present().id)
    position = settings_row.position if settings_row.position in WIDGET_POSITIONS else DEFAULT_WIDGET_POSITION
    css = (
        "/* Спутник: положение иконки виджета. Управляется во вкладке «Виджет» кабинета. */\n"
        f".pcw-root {{ {_position_css(position, 24)} }}\n"
        "@media (max-width: 480px) {\n"
        f"  .pcw-root {{ {_position_css(position, 12)} }}\n"
        "}\n"
    )
    return Response(content=css, media_type="text/css; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.get("/v1/widget/logo")
def widget_logo(store: PlatformStore = Depends(get_platform_store)) -> FileResponse:
    tenant = _present()
    row = store.settings_for(tenant.id)
    if not row.logo_name:
        raise HTTPException(404, "Логотип не задан")
    path = layout_for(Path(_data_root()), tenant.slug).branding_dir / row.logo_name
    if not path.is_file():
        raise HTTPException(404, "Логотип не найден")
    return FileResponse(path)


@router.get("/v1/admin/widget", dependencies=[Depends(require_admin)])
def get_widget(store: PlatformStore = Depends(get_platform_store)) -> dict:
    return _public(store, _cabinet())


@router.put("/v1/admin/widget", dependencies=[Depends(require_admin)])
def put_widget(payload: WidgetSettingsBody, store: PlatformStore = Depends(get_platform_store)) -> dict:
    tenant = _cabinet()
    try:
        store.update_settings(
            tenant.id,
            title=payload.title,
            description=payload.description,
            primary_color=payload.primary_color,
            show_sources=payload.show_sources,
            greeting=payload.greeting,
            disclaimer=payload.disclaimer,
            legal_notes=payload.legal_notes,
            position=payload.position,
        )
    except PlatformError as exc:
        raise HTTPException(400, str(exc)) from exc
    drop_tenant_pipeline(tenant.id)
    return _public(store, tenant)


@router.post("/v1/admin/widget/logo", dependencies=[Depends(require_admin)])
async def upload_logo(
    file: UploadFile = File(...),
    store: PlatformStore = Depends(get_platform_store),
) -> dict:
    tenant = _cabinet()
    suffix = Path(file.filename or "logo.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        raise HTTPException(400, "Логотип: png, jpg, webp или svg")
    layout = layout_for(Path(_data_root()), tenant.slug)
    layout.ensure()
    name = f"logo{suffix}"
    path = layout.branding_dir / name
    path.write_bytes(await file.read())
    current = store.settings_for(tenant.id)
    store.update_settings(
        tenant.id,
        title=current.title,
        description=current.description,
        primary_color=current.primary_color,
        show_sources=current.show_sources,
        greeting=current.greeting,
        disclaimer=current.disclaimer,
        legal_notes=current.legal_notes,
        position=current.position,
        logo_name=name,
    )
    return _public(store, tenant)
