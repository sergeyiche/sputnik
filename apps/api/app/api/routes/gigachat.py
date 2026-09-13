from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from packages.integrations.gigachat.auth import GigaChatAuthError, get_gigachat_auth_service
from packages.integrations.gigachat.client import GigaChatApiError, get_gigachat_client

router = APIRouter(prefix="/v1/gigachat", tags=["gigachat"])


class AuthStatusResponse(BaseModel):
    authenticated: bool
    expires_at: int
    token_preview: str
    scope: str
    api_base_url: str


class AuthTestResponse(BaseModel):
    authenticated: bool
    expires_at: int
    token_preview: str
    models_count: int
    models: list[str]


@router.post("/auth/token", response_model=AuthStatusResponse)
async def obtain_token(force_refresh: bool = False) -> AuthStatusResponse:
    """Получить OAuth access_token по GIGACHAT_AUTHORIZATION_KEY (с кешированием)."""
    try:
        auth = get_gigachat_auth_service()
        token = auth.get_access_token(force_refresh=force_refresh)
    except (GigaChatAuthError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    preview = f"{token.access_token[:12]}…{token.access_token[-8:]}"
    settings = auth.settings

    return AuthStatusResponse(
        authenticated=True,
        expires_at=token.expires_at,
        token_preview=preview,
        scope=settings.scope,
        api_base_url=settings.api_base_url,
    )


@router.get("/auth/test", response_model=AuthTestResponse)
async def test_authentication() -> AuthTestResponse:
    """Первый запрос к API: OAuth → GET /v1/models."""
    try:
        auth = get_gigachat_auth_service()
        token = auth.get_access_token()
        models_payload = get_gigachat_client().list_models()
    except (GigaChatAuthError, GigaChatApiError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    model_items = models_payload.get("data", models_payload.get("models", []))
    model_names = [
        item.get("id") or item.get("name") or str(item)
        for item in model_items
        if isinstance(item, dict)
    ]

    preview = f"{token.access_token[:12]}…{token.access_token[-8:]}"
    return AuthTestResponse(
        authenticated=True,
        expires_at=token.expires_at,
        token_preview=preview,
        models_count=len(model_names),
        models=model_names,
    )
