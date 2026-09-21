from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.app.deps import get_vector_store
from packages.knowledge.base import VectorStore

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeStatusResponse(BaseModel):
    vector_store: str
    collection: str
    chunk_count: int
    knowledge_dir: str
    document_count: int
    documents: list[str] = Field(
        default_factory=list,
        description="Unique document names indexed in the vector store",
    )


def _documents_on_disk(knowledge_dir: Path) -> list[str]:
    if not knowledge_dir.exists():
        return []
    files = list(knowledge_dir.glob("**/*.md")) + list(knowledge_dir.glob("**/*.txt"))
    return sorted({path.name for path in files})


@router.get("/status", response_model=KnowledgeStatusResponse)
async def knowledge_status(
    store: VectorStore = Depends(get_vector_store),
) -> KnowledgeStatusResponse:
    knowledge_dir = Path(os.getenv("KNOWLEDGE_DIR", "./data/knowledge"))

    try:
        chunk_count = store.count()
    except Exception:
        chunk_count = 0

    try:
        documents = store.list_documents()
    except Exception:
        documents = []

    # Fallback to filesystem listing if the store is empty or unavailable
    if not documents:
        documents = _documents_on_disk(knowledge_dir)

    return KnowledgeStatusResponse(
        vector_store=os.getenv("VECTOR_STORE", "chroma"),
        collection=os.getenv("CHROMA_COLLECTION", "parkinson_kb"),
        chunk_count=chunk_count,
        knowledge_dir=str(knowledge_dir),
        document_count=len(documents),
        documents=documents,
    )
