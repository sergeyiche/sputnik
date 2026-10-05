from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from packages.dialogue.suggestions import SuggestionCatalogError, get_starter_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/suggestions", tags=["suggestions"])


class SuggestionsResponse(BaseModel):
    suggestions: list[str]


@router.get("/starter", response_model=SuggestionsResponse)
async def starter_suggestions(
    count: int = Query(3, ge=1, le=10, description="Сколько случайных вопросов вернуть"),
) -> SuggestionsResponse:
    try:
        suggestions = get_starter_catalog().sample(count)
    except SuggestionCatalogError as exc:
        logger.error("Starter suggestions unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Подсказки временно недоступны") from exc
    return SuggestionsResponse(suggestions=suggestions)
