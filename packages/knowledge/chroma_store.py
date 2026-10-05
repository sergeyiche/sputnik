"""ChromaDB implementation of VectorStore."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import chromadb
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from packages.knowledge.base import RetrievedChunk, VectorStore

logger = logging.getLogger(__name__)

_clients: dict[str, chromadb.ClientAPI] = {}
_clients_lock = threading.Lock()


def get_chroma_client(persist_directory: str) -> chromadb.ClientAPI:
    """Return one persistent client per directory for the whole process.

    The SharedSystemClient cache is cleared only before the first client is
    created — stale RustBindingsAPI instances cause
    ``AttributeError: no attribute 'bindings'``, while clearing it later would
    break clients already in use (e.g. by a running import).
    """
    path = Path(persist_directory)
    key = str(path.resolve())
    with _clients_lock:
        client = _clients.get(key)
        if client is not None:
            return client

        path.mkdir(parents=True, exist_ok=True)
        if not _clients:
            try:
                chromadb.api.client.SharedSystemClient.clear_system_cache()
            except Exception:
                pass
        client = chromadb.PersistentClient(path=str(path))
        _clients[key] = client
        return client


def replace_collection(persist_directory: str, staging_name: str, target_name: str) -> None:
    """Atomically-enough swap: drop ``target_name`` and rename ``staging_name`` to it."""
    client = get_chroma_client(persist_directory)
    staging = client.get_collection(staging_name)
    existing = {c.name for c in client.list_collections()}
    if target_name in existing:
        client.delete_collection(target_name)
    staging.modify(name=target_name)
    logger.info("Chroma collection %s replaced by %s", target_name, staging_name)


class ChromaVectorStore(VectorStore):
    def __init__(
        self,
        embeddings: Embeddings,
        persist_directory: str,
        collection_name: str,
    ) -> None:
        client = get_chroma_client(persist_directory)
        self._store = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=embeddings,
        )

    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        self._store.add_texts(texts=texts, metadatas=metadatas, ids=ids)

    def similarity_search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        docs_with_scores = self._store.similarity_search_with_score(query, k=k)
        results: list[RetrievedChunk] = []
        for doc, score in docs_with_scores:
            results.append(
                RetrievedChunk(
                    content=doc.page_content,
                    source=str(doc.metadata.get("source", "unknown")),
                    score=float(score),
                    metadata=dict(doc.metadata),
                )
            )
        return results

    def delete_collection(self) -> None:
        self._store.delete_collection()

    def count(self) -> int:
        collection = getattr(self._store, "_collection", None)
        if collection is None:
            return 0
        return int(collection.count())

    def list_documents(self) -> list[str]:
        collection = getattr(self._store, "_collection", None)
        if collection is None:
            return []

        result = collection.get(include=["metadatas"])
        names: set[str] = set()
        for meta in result.get("metadatas") or []:
            if not meta:
                continue
            raw = meta.get("source") or meta.get("path")
            if not raw:
                continue
            names.add(Path(str(raw)).name)
        return sorted(names)
