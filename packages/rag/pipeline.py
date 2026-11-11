"""RAG pipeline: retrieve context from knowledge base and generate answer."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from packages.knowledge.base import RetrievedChunk, VectorStore
from packages.knowledge.sources import SourceCatalog
from packages.rag.citations import normalize_citations, strip_citations
from packages.rag.prompts import (
    build_rag_user_message,
    get_system_prompt,
    strip_trailing_disclaimer,
)
from packages.security.disclaimer import MEDICAL_DISCLAIMER, append_disclaimer, is_suspicious_query

EMPTY_CONTEXT = "Релевантных фрагментов базы знаний по этому вопросу нет."
HISTORY_MESSAGES = 8


def _token_usage(response: object) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None) or {}
    if isinstance(meta, dict) and (meta.get("input_tokens") or meta.get("output_tokens")):
        return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    raw = getattr(response, "response_metadata", None) or {}
    usage = {}
    if isinstance(raw, dict):
        usage = raw.get("token_usage") or raw.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

SUSPICIOUS_ANSWER = (
    "Я могу помочь только с вопросами по материалам этой организации. "
    "Пожалуйста, переформулируйте ваш вопрос."
)


@dataclass
class RAGResponse:
    answer: str
    sources: list[dict]
    chunks_used: int
    tokens_in: int = 0
    tokens_out: int = 0
    skills: list[dict] | None = None


class RAGPipeline:
    def __init__(
        self,
        llm: BaseChatModel,
        vector_store: VectorStore,
        top_k: int = 5,
        source_catalog: SourceCatalog | None = None,
        prompt_text: Callable[[], str] | None = None,
        disclaimer: str | None = None,
        show_sources: bool = True,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store
        self._top_k = top_k
        self._source_catalog = source_catalog
        self._prompt_text = prompt_text or get_system_prompt
        self._disclaimer = disclaimer or MEDICAL_DISCLAIMER
        self._show_sources = show_sources

    @property
    def llm(self) -> BaseChatModel:
        return self._llm

    def _describe_source(self, document: str) -> dict:
        if self._source_catalog is None:
            return {"source": document, "title": document.rsplit(".", 1)[0], "url": None}
        return self._source_catalog.describe(document)

    def _is_hidden(self, document: str) -> bool:
        return self._source_catalog is not None and self._source_catalog.resolve(document).hidden

    @staticmethod
    def _document_numbers(chunks: list[RetrievedChunk]) -> dict[str, int]:
        """Number documents (not chunks) in order of first appearance."""
        numbers: dict[str, int] = {}
        for chunk in chunks:
            numbers.setdefault(chunk.source, len(numbers) + 1)
        return numbers

    def _format_context(self, chunks: list[RetrievedChunk]) -> str:
        numbers = self._document_numbers(chunks)
        by_document: dict[str, list[str]] = {}
        for chunk in chunks:
            by_document.setdefault(chunk.source, []).append(chunk.content.strip())

        parts: list[str] = []
        for document, number in numbers.items():
            title = self._describe_source(document)["title"]
            body = "\n…\n".join(by_document[document])
            parts.append(f"[{number}] Документ: «{title}»\n{body}")
        return "\n\n".join(parts)

    def _finalize_answer(self, answer: str, chunks: list[RetrievedChunk]) -> tuple[str, list[dict]]:
        """Normalize citations and return only the sources actually cited in the answer."""
        numbers = self._document_numbers(chunks)
        visible = {doc: n for doc, n in numbers.items() if not self._is_hidden(doc)}
        if not self._show_sources:
            return strip_citations(answer), []
        cleaned, cited = normalize_citations(answer, set(visible.values()))

        best_score: dict[str, float | None] = {}
        for chunk in chunks:
            current = best_score.get(chunk.source)
            if current is None or (chunk.score is not None and chunk.score < current):
                best_score[chunk.source] = chunk.score

        by_number = {n: doc for doc, n in visible.items()}
        sources = [
            {"index": number, **self._describe_source(by_number[number]), "score": best_score.get(by_number[number])}
            for number in sorted(cited)
        ]
        return cleaned, sources

    def _retrieve(
        self,
        query: str,
        *,
        boost_sources: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        fetch_k = max(self._top_k, int(os.getenv("RAG_FETCH_K", str(self._top_k * 2))))
        max_distance = os.getenv("RAG_MAX_DISTANCE", "").strip()
        chunks = self._vector_store.similarity_search(query, k=fetch_k)

        if max_distance:
            try:
                limit = float(max_distance)
                # Chroma/LangChain scores here are distances — lower is better
                chunks = [c for c in chunks if c.score is not None and c.score <= limit]
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

        if boost_sources:
            present = {chunk.source for chunk in selected}
            missing = [name for name in boost_sources if name not in present]
            if missing:
                boosted = self._vector_store.chunks_for_sources(missing, limit_per=1)
                # Prepend boosted docs so they get citation numbers when used
                for chunk in reversed(boosted):
                    if chunk.source in present:
                        continue
                    selected.insert(0, chunk)
                    present.add(chunk.source)
                selected = selected[: max(self._top_k, len(present))]

        return selected

    def _history_messages(self, history: list[dict[str, str]] | None) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        if not history:
            return messages
        for msg in history[-HISTORY_MESSAGES:]:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg["role"] == "user":
                messages.append(HumanMessage(content=content))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=strip_citations(strip_trailing_disclaimer(content))))
        return messages

    def _build_messages(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None,
        *,
        topic: str = "",
        meaning: str = "",
        system_prompt: str | None = None,
    ) -> list[BaseMessage]:
        context = self._format_context(chunks) if chunks else EMPTY_CONTEXT
        return [
            SystemMessage(content=system_prompt if system_prompt is not None else self._prompt_text()),
            *self._history_messages(history),
            HumanMessage(
                content=build_rag_user_message(
                    question,
                    context,
                    cite_sources=self._show_sources,
                    topic=topic,
                    meaning=meaning,
                )
            ),
        ]

    def query(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        *,
        search_query: str | None = None,
        topic: str = "",
        meaning: str = "",
        system_prompt: str | None = None,
        boost_sources: list[str] | None = None,
        skills: list[dict] | None = None,
    ) -> RAGResponse:
        if is_suspicious_query(question):
            return RAGResponse(
                answer=append_disclaimer(SUSPICIOUS_ANSWER, self._disclaimer),
                sources=[],
                chunks_used=0,
                skills=skills or [],
            )

        chunks = self._retrieve(
            (search_query or question).strip(),
            boost_sources=boost_sources,
        )

        response = self._llm.invoke(
            self._build_messages(
                question,
                chunks,
                history,
                topic=topic,
                meaning=meaning,
                system_prompt=system_prompt,
            )
        )
        answer_text = response.content if isinstance(response.content, str) else str(response.content)
        answer_text, sources = self._finalize_answer(answer_text, chunks)
        tokens_in, tokens_out = _token_usage(response)

        return RAGResponse(
            answer=append_disclaimer(answer_text, self._disclaimer),
            sources=sources,
            chunks_used=len(chunks),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            skills=skills or [],
        )

    async def astream_query(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        *,
        search_query: str | None = None,
        topic: str = "",
        meaning: str = "",
        system_prompt: str | None = None,
        boost_sources: list[str] | None = None,
        skills: list[dict] | None = None,
    ):
        """Async generator for SSE: yields text tokens, then a final ``{"sources": [...]}`` dict.

        Tokens are sent as generated, so citation markers are not normalized in the stream.
        """
        if is_suspicious_query(question):
            yield append_disclaimer(SUSPICIOUS_ANSWER, self._disclaimer)
            return

        chunks = self._retrieve(
            (search_query or question).strip(),
            boost_sources=boost_sources,
        )

        full_text = ""
        async for token in self._llm.astream(
            self._build_messages(
                question,
                chunks,
                history,
                topic=topic,
                meaning=meaning,
                system_prompt=system_prompt,
            )
        ):
            if token.content:
                text = token.content if isinstance(token.content, str) else str(token.content)
                full_text += text
                yield text

        yield f"\n\n---\n{self._disclaimer}"
        _, sources = self._finalize_answer(full_text, chunks)
        yield {"sources": sources, "skills": skills or []}
