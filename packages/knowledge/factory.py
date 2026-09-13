"""Factory for vector store backends."""

from __future__ import annotations

import os

from langchain_core.embeddings import Embeddings

from packages.knowledge.base import VectorStore
from packages.knowledge.chroma_store import ChromaVectorStore


def create_vector_store(embeddings: Embeddings) -> VectorStore:
    backend = os.getenv("VECTOR_STORE", "chroma").lower()

    if backend == "chroma":
        return ChromaVectorStore(
            embeddings=embeddings,
            persist_directory=os.getenv("CHROMA_PERSIST_DIR", "./data/chroma"),
            collection_name=os.getenv("CHROMA_COLLECTION", "parkinson_kb"),
        )

    if backend == "pgvector":
        raise NotImplementedError(
            "pgvector backend is planned. Set VECTOR_STORE=chroma for MVP."
        )

    raise ValueError(f"Unknown VECTOR_STORE: {backend}")
