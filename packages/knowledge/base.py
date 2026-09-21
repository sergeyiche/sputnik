"""Abstract vector store interface — swap Chroma for pgvector later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievedChunk:
    content: str
    source: str
    score: float | None = None
    metadata: dict[str, Any] | None = None


class VectorStore(ABC):
    @abstractmethod
    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        ...

    @abstractmethod
    def similarity_search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        ...

    @abstractmethod
    def delete_collection(self) -> None:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    @abstractmethod
    def list_documents(self) -> list[str]:
        """Return unique document names currently stored in the index."""
        ...
