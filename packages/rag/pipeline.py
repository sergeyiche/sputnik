"""RAG pipeline: retrieve context from knowledge base and generate answer."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from packages.knowledge.base import RetrievedChunk, VectorStore
from packages.rag.prompts import get_system_prompt
from packages.security.disclaimer import MEDICAL_DISCLAIMER, append_disclaimer, is_suspicious_query

NO_CONTEXT_ANSWER = (
    "К сожалению, в моей базе знаний пока нет достаточной информации для ответа на этот вопрос. "
    "Рекомендую обратиться к неврологу или специалисту по болезни Паркинсона."
)

SUSPICIOUS_ANSWER = (
    "Я могу помочь только с вопросами о болезни Паркинсона на основе проверенных материалов. "
    "Пожалуйста, переформулируйте ваш вопрос."
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

        chunks = self._vector_store.similarity_search(question, k=self._top_k)

        if not chunks:
            return RAGResponse(
                answer=append_disclaimer(NO_CONTEXT_ANSWER),
                sources=[],
                chunks_used=0,
            )

        context = self._format_context(chunks)
        messages = [SystemMessage(content=get_system_prompt())]

        if history:
            for msg in history[-6:]:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        messages.append(
            HumanMessage(
                content=(
                    f"Контекст из базы знаний:\n\n{context}\n\n"
                    f"Вопрос пользователя: {question}\n\n"
                    "Ответь на основе контекста. Если используешь факты — опирайся только на контекст."
                )
            )
        )

        response = self._llm.invoke(messages)
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

        chunks = self._vector_store.similarity_search(question, k=self._top_k)

        if not chunks:
            yield append_disclaimer(NO_CONTEXT_ANSWER)
            return

        context = self._format_context(chunks)
        messages = [SystemMessage(content=get_system_prompt())]

        if history:
            for msg in history[-6:]:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        messages.append(
            HumanMessage(
                content=(
                    f"Контекст из базы знаний:\n\n{context}\n\n"
                    f"Вопрос пользователя: {question}\n\n"
                    "Ответь на основе контекста. Если используешь факты — опирайся только на контекст."
                )
            )
        )

        async for chunk in self._llm.astream(messages):
            if chunk.content:
                yield chunk.content if isinstance(chunk.content, str) else str(chunk.content)

        yield f"\n\n---\n{MEDICAL_DISCLAIMER}"
