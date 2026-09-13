from __future__ import annotations

import os

from packages.dialogue.session import SessionStore
from packages.knowledge.base import VectorStore
from packages.knowledge.embeddings_factory import create_embeddings
from packages.knowledge.factory import create_vector_store
from packages.rag.llm_factory import create_llm
from packages.rag.pipeline import RAGPipeline

_session_store: SessionStore | None = None
_vector_store: VectorStore | None = None
_rag_pipeline: RAGPipeline | None = None
_vector_store_key: str | None = None
_rag_pipeline_key: str | None = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        max_messages = int(os.getenv("SESSION_MAX_MESSAGES", "20"))
        _session_store = SessionStore(max_messages=max_messages)
    return _session_store


def _uses_gigachat_embeddings() -> bool:
    return os.getenv("EMBEDDINGS_PROVIDER", "gigachat").lower() == "gigachat"


def _uses_gigachat_llm() -> bool:
    return os.getenv("LLM_PROVIDER", "gigachat").lower() == "gigachat"


def get_vector_store() -> VectorStore:
    global _vector_store, _vector_store_key

    # credentials-based GigaChatEmbeddings обновляет токен сам — кеш не привязан к access_token
    cache_key = "gigachat" if _uses_gigachat_embeddings() else "local"

    if _vector_store is None or _vector_store_key != cache_key:
        embeddings = create_embeddings()
        _vector_store = create_vector_store(embeddings)
        _vector_store_key = cache_key

    return _vector_store


def get_rag_pipeline() -> RAGPipeline:
    global _rag_pipeline, _rag_pipeline_key

    llm_key = "gigachat" if _uses_gigachat_llm() else os.getenv("LLM_PROVIDER", "local")
    emb_key = "gigachat" if _uses_gigachat_embeddings() else "local"
    cache_key = f"llm:{llm_key}|emb:{emb_key}"

    if _rag_pipeline is None or _rag_pipeline_key != cache_key:
        llm = create_llm()
        vector_store = get_vector_store()
        top_k = int(os.getenv("RAG_TOP_K", "5"))
        _rag_pipeline = RAGPipeline(llm=llm, vector_store=vector_store, top_k=top_k)
        _rag_pipeline_key = cache_key

    return _rag_pipeline


def reset_rag_caches() -> None:
    """Drop cached LLM/embeddings clients (e.g. after forced auth refresh)."""
    global _vector_store, _vector_store_key, _rag_pipeline, _rag_pipeline_key
    _vector_store = None
    _vector_store_key = None
    _rag_pipeline = None
    _rag_pipeline_key = None
