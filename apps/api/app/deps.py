from __future__ import annotations

import os
from pathlib import Path

from langchain_core.embeddings import Embeddings

from apps.api.app.config import settings
from packages.admin.ingest_jobs import IngestDependencies, IngestJobRunner
from packages.admin.knowledge_files import KnowledgeFilesService
from packages.admin.prompt_store import SystemPromptStore, get_prompt_store
from packages.platform.context import tenant_var
from packages.platform.layout import layout_for
from packages.platform.store import PlatformStore, Tenant
from packages.dialogue.session import SessionStore
from packages.etl.ingest import prepare_chunks, store_chunks
from packages.knowledge.base import VectorStore
from packages.knowledge.chroma_store import ChromaVectorStore, replace_collection
from packages.knowledge.embeddings_factory import create_embeddings
from packages.knowledge.factory import create_vector_store
from packages.knowledge.sources import get_source_catalog
from packages.rag.llm_factory import create_llm
from packages.rag.pipeline import RAGPipeline
from packages.integrations.mailtrap import MailtrapMailer
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
_tenant_stores: dict[str, VectorStore] = {}
_tenant_pipelines: dict[int, RAGPipeline] = {}
_tenant_prompts: dict[int, SystemPromptStore] = {}
_tenant_runners: dict[int, IngestJobRunner] = {}
_platform_store: PlatformStore | None = None
_platform_store_path: str | None = None
_mailer: MailtrapMailer | None = None


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
    tenant = _active_tenant()
    if tenant is not None:
        cached = _tenant_stores.get(tenant.collection_name)
        if cached is None:
            cached = ChromaVectorStore(
                embeddings=get_embeddings(),
                persist_directory=_chroma_dir(),
                collection_name=tenant.collection_name,
            )
            _tenant_stores[tenant.collection_name] = cached
        return cached

    global _vector_store, _vector_store_key

    # credentials-based GigaChatEmbeddings обновляет токен сам — кеш не привязан к access_token
    cache_key = "gigachat" if _uses_gigachat_embeddings() else "local"

    if _vector_store is None or _vector_store_key != cache_key:
        _vector_store = create_vector_store(get_embeddings())
        _vector_store_key = cache_key

    return _vector_store


def get_rag_pipeline() -> RAGPipeline:
    _refuse_closed_tenant()
    tenant = _active_tenant()
    if tenant is not None:
        cached = _tenant_pipelines.get(tenant.id)
        if cached is None:
            widget = get_platform_store().settings_for(tenant.id)
            prompt_store = _tenant_prompt_store(tenant)
            cached = RAGPipeline(
                llm=create_llm(),
                vector_store=get_vector_store(),
                top_k=int(os.getenv("RAG_TOP_K", "5")),
                prompt_text=prompt_store.active_text,
                disclaimer=widget.disclaimer,
                show_sources=widget.show_sources,
            )
            _tenant_pipelines[tenant.id] = cached
        return cached

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


def get_system_prompt_store() -> SystemPromptStore:
    tenant = _active_tenant()
    if tenant is not None:
        return _tenant_prompt_store(tenant)
    return get_prompt_store()


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
    tenant = _active_tenant()
    if tenant is not None:
        layout = layout_for(_data_root(), tenant.slug)
        layout.ensure()
        sources, knowledge = layout.sources_dir, layout.knowledge_dir
    else:
        sources, knowledge = _sources_dir(), _knowledge_dir()
    return KnowledgeFilesService(
        sources_dir=sources,
        knowledge_dir=knowledge,
        max_upload_bytes=settings.admin_max_upload_mb * 1024 * 1024,
    )


def get_ingest_runner() -> IngestJobRunner:
    tenant = _active_tenant()
    if tenant is not None:
        runner = _tenant_runners.get(tenant.id)
        if runner is None:
            layout = layout_for(_data_root(), tenant.slug)
            layout.ensure()
            runner = _build_ingest_runner(
                layout.knowledge_dir,
                tenant.collection_name,
                on_success=lambda: _drop_tenant_runtime(tenant.id, tenant.collection_name),
            )
            _tenant_runners[tenant.id] = runner
        return runner

    global _ingest_runner
    if _ingest_runner is None:
        if os.getenv("VECTOR_STORE", "chroma").lower() != "chroma":
            raise NotImplementedError("Импорт из админки поддерживается только для VECTOR_STORE=chroma")
        _ingest_runner = _build_ingest_runner(_knowledge_dir(), _collection_name(), on_success=reset_vector_store)
    return _ingest_runner


def _build_ingest_runner(knowledge_dir: Path, collection_name: str, *, on_success) -> IngestJobRunner:
    if os.getenv("VECTOR_STORE", "chroma").lower() != "chroma":
        raise NotImplementedError("Импорт из админки поддерживается только для VECTOR_STORE=chroma")
    return IngestJobRunner(
        IngestDependencies(
            prepare_chunks=lambda: prepare_chunks(knowledge_dir),
            create_store=lambda name: ChromaVectorStore(
                embeddings=get_embeddings(),
                persist_directory=_chroma_dir(),
                collection_name=name,
            ),
            store_chunks=store_chunks,
            replace_collection=lambda staging, target: replace_collection(_chroma_dir(), staging, target),
            on_success=on_success,
        ),
        collection_name=collection_name,
    )


def _refuse_closed_tenant() -> None:
    from fastapi import HTTPException

    from packages.platform.context import scope_var

    if scope_var.get().kind == "tenant" and _active_tenant() is None:
        raise HTTPException(status_code=404, detail="Организация не найдена или ещё не включена")


def _active_tenant() -> Tenant | None:
    tenant = tenant_var.get()
    if tenant is None or tenant.status != "active":
        return None
    return tenant


def _data_root() -> Path:
    if settings.data_root:
        return Path(settings.data_root)
    from packages.rag.prompts import PROJECT_ROOT

    return PROJECT_ROOT / "data"


def platform_db_path() -> Path:
    if settings.platform_db_path:
        return Path(settings.platform_db_path)
    return _data_root() / "platform" / "platform.db"


def get_platform_store() -> PlatformStore:
    global _platform_store, _platform_store_path
    path = str(platform_db_path())
    if _platform_store is None or _platform_store_path != path:
        _platform_store = PlatformStore(Path(path))
        _platform_store_path = path
    return _platform_store


def reset_platform_store() -> None:
    global _platform_store, _platform_store_path, _mailer
    _platform_store = None
    _platform_store_path = None
    _mailer = None


def get_mailer() -> MailtrapMailer:
    global _mailer
    if _mailer is None:
        _mailer = MailtrapMailer(
            api_key=settings.mailtrap_api_key,
            sender_email=settings.mailtrap_sender_email,
            sender_name=settings.mailtrap_sender_name,
            use_sandbox=settings.mailtrap_use_sandbox,
            inbox_id=settings.mailtrap_inbox_id,
            transport=settings.mail_transport,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
        )
    return _mailer


def _tenant_prompt_store(tenant: Tenant) -> SystemPromptStore:
    cached = _tenant_prompts.get(tenant.id)
    if cached is not None:
        return cached
    layout = layout_for(_data_root(), tenant.slug)
    layout.ensure()
    cached = SystemPromptStore(layout.prompt_file, layout.prompts_dir)
    _tenant_prompts[tenant.id] = cached
    return cached


def _drop_tenant_runtime(tenant_id: int, collection_name: str) -> None:
    _tenant_stores.pop(collection_name, None)
    _tenant_pipelines.pop(tenant_id, None)


def drop_tenant_pipeline(tenant_id: int) -> None:
    _tenant_pipelines.pop(tenant_id, None)
