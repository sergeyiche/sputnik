from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import get_platform_store, get_rag_pipeline, get_session_store, reset_rag_caches
from packages.platform.context import scope_var, tenant_var
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
    http_request: Request,
    session_store: SessionStore = Depends(get_session_store),
    rag: RAGPipeline = Depends(get_rag_pipeline),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    tenant = _require_chat_tenant()
    namespace = tenant.slug if tenant else ""
    session_id = http_request.headers.get("x-session-id") or session_id
    session = session_store.get_or_create(session_id, namespace=namespace)
    history = session_store.get_history(session.session_id, namespace=namespace)

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
        _record_chat_error(tenant, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Не удалось получить ответ от LLM. "
                "Проверьте GIGACHAT_AUTHORIZATION_KEY, GIGACHAT_MODEL "
                f"(доступны: GigaChat-2, GigaChat-2-Pro, GigaChat-2-Max) и индексацию. "
                f"Ошибка: {exc}"
            ),
        ) from exc

    session_store.add_message(session.session_id, "user", request.message, namespace=namespace)
    session_store.add_message(session.session_id, "assistant", result.answer, namespace=namespace)
    _record_chat_usage(tenant, result)

    disclaimer = MEDICAL_DISCLAIMER
    if tenant is not None:
        disclaimer = get_platform_store().settings_for(tenant.id).disclaimer
    payload = ChatResponse(
        answer=result.answer,
        sources=result.sources,
        session_id=session.session_id,
        disclaimer=disclaimer,
    )
    body = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2) + "\n"
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"X-Session-Id": session.session_id},
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
    sources: list[dict] = []

    async def relay(pipeline: RAGPipeline) -> AsyncGenerator[str, None]:
        nonlocal full_answer, sources
        async for item in pipeline.astream_query(message, history=history):
            if isinstance(item, dict):
                sources = item.get("sources", [])
                continue
            full_answer += item
            yield f"data: {json.dumps({'token': item}, ensure_ascii=False)}\n\n"

    try:
        try:
            async for event in relay(rag):
                yield event
        except Exception as exc:
            if not is_gigachat_auth_error(exc) or full_answer:
                raise
            async for event in relay(_refresh_gigachat_clients()):
                yield event
    except Exception as exc:
        error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"data: {error_payload}\n\n"
        return

    session_store.add_message(session.session_id, "user", message)
    session_store.add_message(session.session_id, "assistant", full_answer)
    done_payload = {"done": True, "session_id": session.session_id, "sources": sources}
    yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"


@router.delete("/session")
async def clear_session(
    http_request: Request,
    session_store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> dict:
    tenant = _require_chat_tenant()
    namespace = tenant.slug if tenant else ""
    session_id = http_request.headers.get("x-session-id") or session_id
    if session_id:
        session_store.clear(session_id, namespace=namespace)
    return {"status": "cleared"}


@router.get("/session", response_model=SessionResponse)
async def get_session(
    http_request: Request,
    session_store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> SessionResponse:
    tenant = _require_chat_tenant()
    namespace = tenant.slug if tenant else ""
    session_id = http_request.headers.get("x-session-id") or session_id
    session = session_store.get_or_create(session_id, namespace=namespace)
    return SessionResponse(
        session_id=session.session_id,
        message_count=len(session.messages),
    )


def _require_chat_tenant():
    scope = scope_var.get()
    if scope.kind == "legacy":
        return None
    if scope.kind != "tenant":
        raise HTTPException(status_code=404, detail="Ассистент доступен на поддомене организации")
    tenant = tenant_var.get()
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=404, detail="Организация не найдена или ещё не включена")
    return tenant


def _record_chat_usage(tenant, result) -> None:
    if tenant is None:
        return
    get_platform_store().record_usage(
        tenant.id,
        questions=1,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        empty_retrievals=1 if result.chunks_used == 0 else 0,
    )


def _record_chat_error(tenant, exc: Exception) -> None:
    if tenant is None:
        return
    text = str(exc)
    get_platform_store().record_usage(
        tenant.id,
        errors=1,
        http_429=1 if "429" in text else 0,
        http_402=1 if "402" in text else 0,
    )
