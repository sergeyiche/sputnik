"""Public widget appearance and the tenant admin that edits it."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from apps.api.app.api.routes.admin import require_admin
from apps.api.app.config import settings
from apps.api.app.deps import drop_tenant_pipeline, get_platform_store
from apps.api.app.deps import _data_root
from packages.platform.context import scope_var, tenant_var
from packages.platform.layout import layout_for
from packages.platform.store import PlatformError, PlatformStore

router = APIRouter(tags=["widget"])


class WidgetSettingsBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=150)
    primary_color: str = Field(default="#fc7f03", max_length=16)
    show_sources: bool = True
    disclaimer: str = Field(..., min_length=1, max_length=250)
    legal_notes: str = Field(default="", max_length=3000)


def _active():
    tenant = tenant_var.get()
    if scope_var.get().kind != "tenant" or tenant is None or tenant.status != "active":
        raise HTTPException(404, "Организация не найдена или ещё не включена")
    return tenant


def _public(store: PlatformStore, tenant) -> dict:
    settings_row = store.settings_for(tenant.id)
    origin = f"https://{tenant.slug}.{settings.platform_base_domain}"
    return {
        "title": settings_row.title,
        "description": settings_row.description,
        "primary_color": settings_row.primary_color,
        "show_sources": settings_row.show_sources,
        "disclaimer": settings_row.disclaimer,
        "legal_notes": settings_row.legal_notes,
        "logo_url": f"{origin}/v1/widget/logo" if settings_row.logo_name else "",
        "origin": origin,
        "snippet": (
            f'<link rel="stylesheet" href="{origin}/widget/sputnik.css" />\n'
            f'<script src="{origin}/widget/sputnik.js" defer></script>\n'
            "<sputnik-widget></sputnik-widget>"
        ),
    }


@router.get("/v1/widget/config")
def widget_config(store: PlatformStore = Depends(get_platform_store)) -> dict:
    return _public(store, _active())


@router.get("/v1/widget/logo")
def widget_logo(store: PlatformStore = Depends(get_platform_store)) -> FileResponse:
    tenant = _active()
    row = store.settings_for(tenant.id)
    if not row.logo_name:
        raise HTTPException(404, "Логотип не задан")
    path = layout_for(Path(_data_root()), tenant.slug).branding_dir / row.logo_name
    if not path.is_file():
        raise HTTPException(404, "Логотип не найден")
    return FileResponse(path)


@router.get("/v1/admin/widget", dependencies=[Depends(require_admin)])
def get_widget(store: PlatformStore = Depends(get_platform_store)) -> dict:
    return _public(store, _active())


@router.put("/v1/admin/widget", dependencies=[Depends(require_admin)])
def put_widget(payload: WidgetSettingsBody, store: PlatformStore = Depends(get_platform_store)) -> dict:
    tenant = _active()
    try:
        store.update_settings(
            tenant.id,
            title=payload.title,
            description=payload.description,
            primary_color=payload.primary_color,
            show_sources=payload.show_sources,
            disclaimer=payload.disclaimer,
            legal_notes=payload.legal_notes,
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
    tenant = _active()
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
        disclaimer=current.disclaimer,
        legal_notes=current.legal_notes,
        logo_name=name,
    )
    return _public(store, tenant)
