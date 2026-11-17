"""Abstract vector store interface — swap Chroma for pgvector later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Converter writes these lines at the top of every normalized .txt. They carry
# no knowledge and must not become a retrievable chunk on their own.
CONVERSION_HEADER_KEYS = frozenset({"source-file", "source-format", "converted-at"})


def _conversion_header_end(text: str) -> int:
    """Return the number of leading lines that belong to the converter header."""
    lines = text.splitlines()
    keys: set[str] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("# "):
            break
        key, separator, _value = line[2:].partition(":")
        if not separator:
            break
        keys.add(key.strip().lower())
        index += 1
    if not keys & CONVERSION_HEADER_KEYS:
        return 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def strip_conversion_header(text: str) -> str:
    """Drop the ``# source-file`` preamble before chunking a document."""
    end = _conversion_header_end(text)
    if end == 0:
        return text
    return "\n".join(text.splitlines()[end:])


def is_conversion_header(text: str) -> bool:
    """True when a chunk contains only converter metadata (no document body)."""
    if not text.strip():
        return False
    end = _conversion_header_end(text)
    if end == 0:
        return False
    return not "\n".join(text.splitlines()[end:]).strip()


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

    def chunks_for_sources(self, sources: list[str], *, limit_per: int = 1) -> list[RetrievedChunk]:
        """Return up to ``limit_per`` chunks for each document name (skill retrieval boost)."""
        return []
