"""ChromaDB implementation of VectorStore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from packages.knowledge.base import RetrievedChunk, VectorStore


def _create_chroma_client(persist_directory: str) -> chromadb.ClientAPI:
    """Create a persistent Chroma client.

    Clears SharedSystemClient cache first — stale/broken RustBindingsAPI
    instances in the process cause: AttributeError: no attribute 'bindings'.
    """
    path = Path(persist_directory)
    path.mkdir(parents=True, exist_ok=True)

    try:
        chromadb.api.client.SharedSystemClient.clear_system_cache()
    except Exception:
        pass

    return chromadb.PersistentClient(path=str(path))


class ChromaVectorStore(VectorStore):
    def __init__(
        self,
        embeddings: Embeddings,
        persist_directory: str,
        collection_name: str,
    ) -> None:
        client = _create_chroma_client(persist_directory)
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
