from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import get_rag_pipeline, get_session_store, reset_rag_caches
from packages.dialogue.session import SessionStore
from packages.integrations.gigachat.auth import get_gigachat_auth_service
from packages.integrations.gigachat.errors import is_gigachat_auth_error
from packages.rag.pipeline import RAGPipeline
from packages.security.disclaimer import MEDICAL_DISCLAIMER

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    stream: bool = False


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    session_id: str
    disclaimer: str = MEDICAL_DISCLAIMER


class SessionResponse(BaseModel):
    session_id: str
    message_count: int


@router.post("/chat", response_model=None)
async def chat(
    request: ChatRequest,
    response: Response,
    session_store: SessionStore = Depends(get_session_store),
    rag: RAGPipeline = Depends(get_rag_pipeline),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    session = session_store.get_or_create(session_id)
    history = session_store.get_history(session.session_id)

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.session_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )

    if request.stream:
        return StreamingResponse(
            _stream_answer(rag, request.message, history, session, session_store),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Session-Id": session.session_id,
            },
        )

    try:
        result = _query_with_auth_retry(rag, request.message, history)
    except Exception as exc:
        # Логируем полный текст, чтобы в uvicorn было видно причину (модель, auth, SSL…)
        import logging

        logging.getLogger("uvicorn.error").exception("Chat failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Не удалось получить ответ от LLM. "
                "Проверьте GIGACHAT_AUTHORIZATION_KEY, GIGACHAT_MODEL "
                f"(доступны: GigaChat-2, GigaChat-2-Pro, GigaChat-2-Max) и индексацию. "
                f"Ошибка: {exc}"
            ),
        ) from exc

    session_store.add_message(session.session_id, "user", request.message)
    session_store.add_message(session.session_id, "assistant", result.answer)

    payload = ChatResponse(
        answer=result.answer,
        sources=result.sources,
        session_id=session.session_id,
    )
    # Многострочный JSON: однострочный ответ (~3KB) часто не отображается в Cursor terminal
    body = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2) + "\n"
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
    )


def _refresh_gigachat_clients() -> RAGPipeline:
    """Invalidate cached OAuth token and rebuild LLM/embeddings clients."""
    import logging

    logging.getLogger("uvicorn.error").warning(
        "GigaChat auth error — invalidating token and rebuilding RAG clients"
    )
    try:
        get_gigachat_auth_service().invalidate()
    except Exception:
        pass
    reset_rag_caches()
    return get_rag_pipeline()


def _query_with_auth_retry(
    rag: RAGPipeline,
    message: str,
    history: list[dict[str, str]],
) -> RAGResponse:
    try:
        return rag.query(message, history=history)
    except Exception as exc:
        if not is_gigachat_auth_error(exc):
            raise
        rag = _refresh_gigachat_clients()
        return rag.query(message, history=history)


async def _stream_answer(
    rag: RAGPipeline,
    message: str,
    history: list[dict[str, str]],
    session,
    session_store: SessionStore,
) -> AsyncGenerator[str, None]:
    full_answer = ""
    try:
        try:
            stream = rag.astream_query(message, history=history)
            async for token in stream:
                full_answer += token
                payload = json.dumps({"token": token}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as exc:
            if not is_gigachat_auth_error(exc) or full_answer:
                raise
            rag = _refresh_gigachat_clients()
            async for token in rag.astream_query(message, history=history):
                full_answer += token
                payload = json.dumps({"token": token}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
    except Exception as exc:
        error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"data: {error_payload}\n\n"
        return

    session_store.add_message(session.session_id, "user", message)
    session_store.add_message(session.session_id, "assistant", full_answer)
    yield f"data: {json.dumps({'done': True, 'session_id': session.session_id}, ensure_ascii=False)}\n\n"


@router.delete("/session")
async def clear_session(
    session_store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> dict:
    if session_id:
        session_store.clear(session_id)
    return {"status": "cleared"}


@router.get("/session", response_model=SessionResponse)
async def get_session(
    session_store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> SessionResponse:
    session = session_store.get_or_create(session_id)
    return SessionResponse(
        session_id=session.session_id,
        message_count=len(session.messages),
    )
