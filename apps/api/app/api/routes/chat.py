from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import (
    get_dialogue_store,
    get_platform_store,
    get_rag_pipeline,
    get_skill_router,
    get_sticky_skills,
    get_system_prompt_store,
    reset_rag_caches,
)
from packages.platform.context import scope_var, tenant_var
from packages.dialogue.followup import fallback_standalone, is_followup, resolve_turn
from packages.dialogue.memory import MemoryFact, extract_facts, inject_memory_block
from packages.dialogue.store import DialogueStore
from packages.integrations.gigachat.auth import get_gigachat_auth_service
from packages.integrations.gigachat.errors import is_gigachat_auth_error
from packages.rag.pipeline import RAGPipeline, RAGResponse
from packages.security.disclaimer import MEDICAL_DISCLAIMER, is_suspicious_query
from packages.skills.runtime import (
    SkillTurn,
    apply_skills,
    get_context_skill,
    is_attention_enabled,
    memory_config,
)

router = APIRouter(prefix="/v1", tags=["chat"])

_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    stream: bool = False


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    session_id: str
    disclaimer: str = MEDICAL_DISCLAIMER
    skills: list[dict] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class SessionMessageModel(BaseModel):
    role: str
    content: str
    sources: list[dict] = Field(default_factory=list)


class SessionResponse(BaseModel):
    session_id: str
    messages: list[SessionMessageModel]
    message_count: int


@dataclass
class PreparedTurn:
    session_id: str
    history: list[dict[str, str]]
    search_query: str
    new_topic: str
    prompt_topic: str
    prompt_meaning: str
    skill_turn: SkillTurn | None = None


@router.post("/chat", response_model=None)
async def chat(
    request: ChatRequest,
    http_request: Request,
    dialogue: DialogueStore = Depends(get_dialogue_store),
    rag: RAGPipeline = Depends(get_rag_pipeline),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    tenant = _require_chat_tenant()
    tenant_id = None if tenant is None else tenant.id
    prepared = _prepare_turn(
        rag,
        dialogue,
        tenant_id,
        http_request.headers.get("x-session-id") or session_id,
        request.message,
    )

    if request.stream:
        streamed = StreamingResponse(
            _stream_answer(rag, dialogue, tenant_id, prepared, request.message, tenant),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Session-Id": prepared.session_id,
            },
        )
        _remember_session(streamed, prepared.session_id)
        return streamed

    skill = prepared.skill_turn
    try:
        result = _query_with_auth_retry(
            rag,
            request.message,
            prepared.history,
            search_query=(skill.search_query if skill else prepared.search_query),
            topic=prepared.prompt_topic,
            meaning=prepared.prompt_meaning,
            system_prompt=(skill.system_prompt if skill else None),
            boost_sources=(skill.boost_sources if skill else None),
            skills=(skill.payload() if skill else []),
        )
    except Exception as exc:
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

    sid = dialogue.record_exchange(
        prepared.session_id,
        tenant_id=tenant_id,
        user_text=request.message,
        answer_text=result.answer,
        sources=result.sources,
        topic=prepared.new_topic,
    )
    _persist_memories(
        dialogue,
        rag,
        store=get_platform_store(),
        tenant_id=tenant_id,
        session_id=sid,
        user_text=request.message,
    )
    _record_chat_usage(tenant, result)

    disclaimer = MEDICAL_DISCLAIMER
    if tenant is not None:
        disclaimer = get_platform_store().settings_for(tenant.id).disclaimer
    payload = ChatResponse(
        answer=result.answer,
        sources=result.sources,
        session_id=sid,
        disclaimer=disclaimer,
        skills=result.skills or [],
        suggestions=list(skill.suggestions) if skill else [],
    )
    body = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2) + "\n"
    answered = Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"X-Session-Id": sid},
    )
    _remember_session(answered, sid)
    return answered


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
    *,
    search_query: str,
    topic: str,
    meaning: str,
    system_prompt: str | None = None,
    boost_sources: list[str] | None = None,
    skills: list[dict] | None = None,
) -> RAGResponse:
    kwargs = dict(
        search_query=search_query,
        topic=topic,
        meaning=meaning,
        system_prompt=system_prompt,
        boost_sources=boost_sources,
        skills=skills,
    )
    try:
        return rag.query(message, history=history, **kwargs)
    except Exception as exc:
        if not is_gigachat_auth_error(exc):
            raise
        rag = _refresh_gigachat_clients()
        return rag.query(message, history=history, **kwargs)


def _prepare_turn(
    rag: RAGPipeline,
    dialogue: DialogueStore,
    tenant_id: int | None,
    raw_session_id: str | None,
    question: str,
) -> PreparedTurn:
    session_key = _clean_session_id(raw_session_id)
    existing = dialogue.get(session_key, tenant_id=tenant_id) if session_key else None
    history = [{"role": item.role, "content": item.content} for item in existing.messages] if existing else []
    topic = existing.topic if existing else ""
    session_id = existing.session_id if existing else (session_key or str(uuid.uuid4()))

    if is_suspicious_query(question):
        return PreparedTurn(
            session_id=session_id,
            history=history,
            search_query=question.strip(),
            new_topic=topic or question.strip(),
            prompt_topic="",
            prompt_meaning="",
        )

    store = get_platform_store()
    attention_on = is_attention_enabled(store, tenant_id)
    clarification = bool(attention_on and history and is_followup(question))
    if clarification:
        search_query, new_topic = _resolve(rag, topic, history, question)
    else:
        text = question.strip()
        search_query, new_topic = text, text

    base_prompt = get_system_prompt_store().active_text()
    skill_turn = apply_skills(
        store=store,
        router_factory=get_skill_router,
        sticky=get_sticky_skills(),
        tenant_id=tenant_id,
        session_id=session_id,
        question=question,
        search_query=search_query,
        base_prompt=base_prompt,
        # New standalone question must not keep previous domain sticky.
        reset_sticky=not clarification,
    )
    skill_turn = _attach_memory_facts(
        dialogue,
        store=store,
        tenant_id=tenant_id,
        session_id=session_id,
        skill_turn=skill_turn,
        base_prompt=base_prompt,
    )
    return PreparedTurn(
        session_id=session_id,
        history=history,
        search_query=search_query,
        new_topic=new_topic,
        prompt_topic=(topic or new_topic) if clarification else "",
        prompt_meaning=search_query if clarification else "",
        skill_turn=skill_turn,
    )


def _attach_memory_facts(
    dialogue: DialogueStore,
    *,
    store,
    tenant_id: int | None,
    session_id: str,
    skill_turn: SkillTurn,
    base_prompt: str,
) -> SkillTurn:
    memory_skill = get_context_skill(store, tenant_id, "dialogue-memory")
    if memory_skill is None:
        return skill_turn
    cfg = memory_config(memory_skill)
    rows = dialogue.list_memories(session_id, tenant_id=tenant_id, limit=cfg["max_facts"])
    facts = [
        MemoryFact(
            key=row.key,
            value=row.value,
            source=row.source,
            confidence=row.confidence,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    prompt = skill_turn.system_prompt if skill_turn.system_prompt is not None else base_prompt
    prompt = inject_memory_block(prompt, facts, limit=cfg["max_facts"])
    return SkillTurn(
        matched=skill_turn.matched,
        system_prompt=prompt,
        search_query=skill_turn.search_query,
        boost_sources=skill_turn.boost_sources,
        suggestions=skill_turn.suggestions,
    )


def _persist_memories(
    dialogue: DialogueStore,
    rag: RAGPipeline,
    *,
    store,
    tenant_id: int | None,
    session_id: str,
    user_text: str,
) -> None:
    memory_skill = get_context_skill(store, tenant_id, "dialogue-memory")
    if memory_skill is None:
        return
    cfg = memory_config(memory_skill)
    llm = getattr(rag, "llm", None) if cfg["use_llm_extract"] else None
    for key, value, confidence in extract_facts(
        user_text,
        llm=llm,
        use_llm=cfg["use_llm_extract"],
    ):
        dialogue.upsert_memory(
            session_id,
            tenant_id=tenant_id,
            key=key,
            value=value,
            source="extracted",
            confidence=confidence,
        )


def _resolve(
    rag: RAGPipeline,
    topic: str,
    history: list[dict[str, str]],
    question: str,
) -> tuple[str, str]:
    def attempt(pipeline: RAGPipeline) -> tuple[str, str]:
        return resolve_turn(getattr(pipeline, "llm", None), topic=topic, history=history, question=question)

    try:
        return attempt(rag)
    except Exception as exc:
        if not is_gigachat_auth_error(exc):
            raise
        try:
            return attempt(_refresh_gigachat_clients())
        except Exception:
            anchor = topic.strip()
            if history and is_followup(question):
                return fallback_standalone(anchor, question), anchor or question.strip()
            text = question.strip()
            return text, text


async def _stream_answer(
    rag: RAGPipeline,
    dialogue: DialogueStore,
    tenant_id: int | None,
    prepared: PreparedTurn,
    message: str,
    tenant,
) -> AsyncGenerator[str, None]:
    full_answer = ""
    sources: list[dict] = []
    skills: list[dict] = prepared.skill_turn.payload() if prepared.skill_turn else []

    async def relay(pipeline: RAGPipeline) -> AsyncGenerator[str, None]:
        nonlocal full_answer, sources, skills
        skill = prepared.skill_turn
        async for item in pipeline.astream_query(
            message,
            history=prepared.history,
            search_query=(skill.search_query if skill else prepared.search_query),
            topic=prepared.prompt_topic,
            meaning=prepared.prompt_meaning,
            system_prompt=(skill.system_prompt if skill else None),
            boost_sources=(skill.boost_sources if skill else None),
            skills=skills,
        ):
            if isinstance(item, dict):
                sources = item.get("sources", [])
                skills = item.get("skills", skills)
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
        _record_chat_error(tenant, exc)
        error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"data: {error_payload}\n\n"
        return

    sid = dialogue.record_exchange(
        prepared.session_id,
        tenant_id=tenant_id,
        user_text=message,
        answer_text=full_answer,
        sources=sources,
        topic=prepared.new_topic,
    )
    _persist_memories(
        dialogue,
        rag,
        store=get_platform_store(),
        tenant_id=tenant_id,
        session_id=sid,
        user_text=message,
    )
    suggestions = list(prepared.skill_turn.suggestions) if prepared.skill_turn else []
    done_payload = {
        "done": True,
        "session_id": sid,
        "sources": sources,
        "skills": skills,
        "suggestions": suggestions,
    }
    yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"


@router.delete("/session")
async def clear_session() -> dict:
    """The widget starts a new id locally. Stored transcripts stay for review."""
    return {"status": "cleared"}


@router.get("/session", response_model=SessionResponse)
async def get_session(
    response: Response,
    http_request: Request,
    dialogue: DialogueStore = Depends(get_dialogue_store),
    session_id: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> SessionResponse:
    tenant = _require_chat_tenant()
    tenant_id = None if tenant is None else tenant.id
    raw = _clean_session_id(http_request.headers.get("x-session-id") or session_id)
    session_id = raw or str(uuid.uuid4())
    existing = dialogue.get(session_id, tenant_id=tenant_id) if raw else None
    messages = existing.messages if existing else []
    _remember_session(response, existing.session_id if existing else session_id)
    return SessionResponse(
        session_id=existing.session_id if existing else session_id,
        messages=[
            SessionMessageModel(role=item.role, content=item.content, sources=item.sources) for item in messages
        ],
        message_count=len(messages),
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


def _clean_session_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if _SESSION_ID.fullmatch(text):
        return text
    return None


def _remember_session(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 400,
    )


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
