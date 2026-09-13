from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.app.deps import get_vector_store
from packages.knowledge.base import VectorStore

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeStatusResponse(BaseModel):
    vector_store: str
    collection: str
    chunk_count: int
    knowledge_dir: str
    document_count: int


@router.get("/status", response_model=KnowledgeStatusResponse)
async def knowledge_status(
    store: VectorStore = Depends(get_vector_store),
) -> KnowledgeStatusResponse:
    knowledge_dir = Path(os.getenv("KNOWLEDGE_DIR", "./data/knowledge"))
    doc_count = 0
    if knowledge_dir.exists():
        doc_count = len(list(knowledge_dir.glob("**/*.md"))) + len(
            list(knowledge_dir.glob("**/*.txt"))
        )

    try:
        chunk_count = store.count()
    except Exception:
        chunk_count = 0

    return KnowledgeStatusResponse(
        vector_store=os.getenv("VECTOR_STORE", "chroma"),
        collection=os.getenv("CHROMA_COLLECTION", "parkinson_kb"),
        chunk_count=chunk_count,
        knowledge_dir=str(knowledge_dir),
        document_count=doc_count,
    )
