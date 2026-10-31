from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.app.deps import get_platform_store
from packages.dialogue.suggestions import SuggestionCatalogError, get_starter_catalog
from packages.platform.context import scope_var, tenant_var
from packages.platform.store import PlatformStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/suggestions", tags=["suggestions"])


class SuggestionsResponse(BaseModel):
    suggestions: list[str]


@router.get("/starter", response_model=SuggestionsResponse)
async def starter_suggestions(
    count: int = Query(3, ge=1, le=10, description="Сколько случайных вопросов вернуть"),
    store: PlatformStore = Depends(get_platform_store),
) -> SuggestionsResponse:
    scope = scope_var.get()
    tenant = tenant_var.get()
    if scope.kind == "tenant":
        if tenant is None:
            raise HTTPException(status_code=404, detail="Организация не найдена")
        return SuggestionsResponse(suggestions=store.sample_starter_questions(tenant.id, count))
    try:
        suggestions = get_starter_catalog().sample(count)
    except SuggestionCatalogError as exc:
        logger.error("Starter suggestions unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Подсказки временно недоступны") from exc
    return SuggestionsResponse(suggestions=suggestions)
