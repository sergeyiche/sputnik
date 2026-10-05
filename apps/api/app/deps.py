from __future__ import annotations

import os
from pathlib import Path

from langchain_core.embeddings import Embeddings

from apps.api.app.config import settings
from packages.admin.ingest_jobs import IngestDependencies, IngestJobRunner
from packages.admin.knowledge_files import KnowledgeFilesService
from packages.dialogue.session import SessionStore
from packages.etl.ingest import prepare_chunks, store_chunks
from packages.knowledge.base import VectorStore
from packages.knowledge.chroma_store import ChromaVectorStore, replace_collection
from packages.knowledge.embeddings_factory import create_embeddings
from packages.knowledge.factory import create_vector_store
from packages.knowledge.sources import get_source_catalog
from packages.rag.llm_factory import create_llm
from packages.rag.pipeline import RAGPipeline
from packages.security.admin_auth import AdminAuthService

_session_store: SessionStore | None = None
_embeddings: Embeddings | None = None
_embeddings_key: str | None = None
_vector_store: VectorStore | None = None
_rag_pipeline: RAGPipeline | None = None
_vector_store_key: str | None = None
_rag_pipeline_key: str | None = None
_admin_auth: AdminAuthService | None = None
_ingest_runner: IngestJobRunner | None = None


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


def get_embeddings() -> Embeddings:
    """Shared embeddings client — the local model is loaded into memory only once."""
    global _embeddings, _embeddings_key
    cache_key = "gigachat" if _uses_gigachat_embeddings() else "local"
    if _embeddings is None or _embeddings_key != cache_key:
        _embeddings = create_embeddings()
        _embeddings_key = cache_key
    return _embeddings


def get_vector_store() -> VectorStore:
    global _vector_store, _vector_store_key

    # credentials-based GigaChatEmbeddings обновляет токен сам — кеш не привязан к access_token
    cache_key = "gigachat" if _uses_gigachat_embeddings() else "local"

    if _vector_store is None or _vector_store_key != cache_key:
        _vector_store = create_vector_store(get_embeddings())
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
        _rag_pipeline = RAGPipeline(
            llm=llm,
            vector_store=vector_store,
            top_k=top_k,
            source_catalog=get_source_catalog(),
        )
        _rag_pipeline_key = cache_key

    return _rag_pipeline


def reset_vector_store() -> None:
    """Drop cached store/pipeline so the next request opens the (replaced) collection."""
    global _vector_store, _vector_store_key, _rag_pipeline, _rag_pipeline_key
    _vector_store = None
    _vector_store_key = None
    _rag_pipeline = None
    _rag_pipeline_key = None


def reset_rag_caches() -> None:
    """Drop cached LLM/embeddings clients (e.g. after forced auth refresh)."""
    global _embeddings, _embeddings_key
    _embeddings = None
    _embeddings_key = None
    reset_vector_store()


def _knowledge_dir() -> Path:
    return Path(os.getenv("KNOWLEDGE_DIR", "./data/knowledge"))


def _sources_dir() -> Path:
    return Path(os.getenv("KNOWLEDGE_SOURCES_DIR", "./data/knowledge_sources"))


def _chroma_dir() -> str:
    return os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")


def _collection_name() -> str:
    return os.getenv("CHROMA_COLLECTION", "parkinson_kb")


def get_admin_auth() -> AdminAuthService:
    global _admin_auth
    if _admin_auth is None:
        _admin_auth = AdminAuthService(
            username=settings.admin_username,
            password=settings.admin_password,
            session_ttl_seconds=settings.admin_session_ttl_minutes * 60,
        )
    return _admin_auth


def get_knowledge_files_service() -> KnowledgeFilesService:
    return KnowledgeFilesService(
        sources_dir=_sources_dir(),
        knowledge_dir=_knowledge_dir(),
        max_upload_bytes=settings.admin_max_upload_mb * 1024 * 1024,
    )


def get_ingest_runner() -> IngestJobRunner:
    global _ingest_runner
    if _ingest_runner is None:
        if os.getenv("VECTOR_STORE", "chroma").lower() != "chroma":
            raise NotImplementedError("Импорт из админки поддерживается только для VECTOR_STORE=chroma")
        _ingest_runner = IngestJobRunner(
            IngestDependencies(
                prepare_chunks=lambda: prepare_chunks(_knowledge_dir()),
                create_store=lambda name: ChromaVectorStore(
                    embeddings=get_embeddings(),
                    persist_directory=_chroma_dir(),
                    collection_name=name,
                ),
                store_chunks=store_chunks,
                replace_collection=lambda staging, target: replace_collection(_chroma_dir(), staging, target),
                on_success=reset_vector_store,
            ),
            collection_name=_collection_name(),
        )
    return _ingest_runner
