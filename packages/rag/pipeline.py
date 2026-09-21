"""RAG pipeline: retrieve context from knowledge base and generate answer."""

from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from packages.knowledge.base import RetrievedChunk, VectorStore
from packages.rag.prompts import (
    build_rag_user_message,
    get_system_prompt,
    strip_trailing_disclaimer,
)
from packages.security.disclaimer import MEDICAL_DISCLAIMER, append_disclaimer, is_suspicious_query

NO_CONTEXT_ANSWER = (
    "В доступных материалах фонда пока нет достаточной информации, чтобы уверенно ответить на этот вопрос. "
    "Рекомендую уточнить его у невролога или специалиста по болезни Паркинсона. "
    "Если хотите, переформулируйте вопрос — попробую найти ответ в других разделах."
)

SUSPICIOUS_ANSWER = (
    "Я могу помочь только с вопросами о болезни Паркинсона и поддержке на основе материалов фонда. "
    "Пожалуйста, переформулируйте ваш вопрос в этой теме."
)


@dataclass
class RAGResponse:
    answer: str
    sources: list[dict]
    chunks_used: int


class RAGPipeline:
    def __init__(
        self,
        llm: BaseChatModel,
        vector_store: VectorStore,
        top_k: int = 5,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store
        self._top_k = top_k

    def _format_context(self, chunks: list[RetrievedChunk]) -> str:
        parts: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            parts.append(f"[{i}] Источник: {chunk.source}\n{chunk.content}")
        return "\n\n".join(parts)

    def _build_sources(self, chunks: list[RetrievedChunk]) -> list[dict]:
        seen: set[str] = set()
        sources: list[dict] = []
        for chunk in chunks:
            if chunk.source in seen:
                continue
            seen.add(chunk.source)
            sources.append({"source": chunk.source, "score": chunk.score})
        return sources

    def _retrieval_query(self, question: str, history: list[dict[str, str]] | None) -> str:
        """Enrich short follow-ups with the previous user turn for better search."""
        q = question.strip()
        if not history:
            return q
        prev_user = next(
            (m["content"].strip() for m in reversed(history) if m.get("role") == "user" and m.get("content")),
            "",
        )
        if not prev_user or prev_user == q:
            return q
        # Keep retrieval query compact
        if len(q) < 80:
            return f"{prev_user}\n{q}"
        return q

    def _retrieve(self, question: str, history: list[dict[str, str]] | None) -> list[RetrievedChunk]:
        fetch_k = max(self._top_k, int(os.getenv("RAG_FETCH_K", str(self._top_k * 2))))
        max_distance = os.getenv("RAG_MAX_DISTANCE", "").strip()
        query = self._retrieval_query(question, history)
        chunks = self._vector_store.similarity_search(query, k=fetch_k)

        if max_distance:
            try:
                limit = float(max_distance)
                # Chroma/LangChain scores here are distances — lower is better
                chunks = [c for c in chunks if c.score <= limit]
            except ValueError:
                pass

        # Prefer source diversity among top results
        selected: list[RetrievedChunk] = []
        per_source: dict[str, int] = {}
        for chunk in chunks:
            count = per_source.get(chunk.source, 0)
            if count >= 2 and len(selected) >= self._top_k // 2:
                continue
            selected.append(chunk)
            per_source[chunk.source] = count + 1
            if len(selected) >= self._top_k:
                break

        if len(selected) < min(self._top_k, len(chunks)):
            for chunk in chunks:
                if chunk in selected:
                    continue
                selected.append(chunk)
                if len(selected) >= self._top_k:
                    break

        return selected

    def _history_messages(self, history: list[dict[str, str]] | None) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        if not history:
            return messages
        for msg in history[-6:]:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg["role"] == "user":
                messages.append(HumanMessage(content=content))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=strip_trailing_disclaimer(content)))
        return messages

    def _build_messages(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None,
    ) -> list[BaseMessage]:
        context = self._format_context(chunks)
        return [
            SystemMessage(content=get_system_prompt()),
            *self._history_messages(history),
            HumanMessage(content=build_rag_user_message(question, context)),
        ]

    def query(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> RAGResponse:
        if is_suspicious_query(question):
            return RAGResponse(
                answer=append_disclaimer(SUSPICIOUS_ANSWER),
                sources=[],
                chunks_used=0,
            )

        chunks = self._retrieve(question, history)

        if not chunks:
            return RAGResponse(
                answer=append_disclaimer(NO_CONTEXT_ANSWER),
                sources=[],
                chunks_used=0,
            )

        response = self._llm.invoke(self._build_messages(question, chunks, history))
        answer_text = response.content if isinstance(response.content, str) else str(response.content)

        return RAGResponse(
            answer=append_disclaimer(answer_text),
            sources=self._build_sources(chunks),
            chunks_used=len(chunks),
        )

    async def astream_query(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ):
        """Async generator yielding text tokens for SSE streaming."""
        if is_suspicious_query(question):
            yield append_disclaimer(SUSPICIOUS_ANSWER)
            return

        chunks = self._retrieve(question, history)

        if not chunks:
            yield append_disclaimer(NO_CONTEXT_ANSWER)
            return

        async for chunk in self._llm.astream(self._build_messages(question, chunks, history)):
            if chunk.content:
                yield chunk.content if isinstance(chunk.content, str) else str(chunk.content)

        yield f"\n\n---\n{MEDICAL_DISCLAIMER}"
