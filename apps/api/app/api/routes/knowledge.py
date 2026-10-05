from __future__ import annotations

import os
from pathlib import Path

import logging
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from apps.api.app.deps import get_vector_store
from packages.knowledge.base import VectorStore
from packages.knowledge.sources import SourceCatalog, get_source_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

INLINE_MEDIA_TYPES = {"application/pdf", "text/plain", "text/markdown"}
EXTRA_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".doc": "application/msword",
    ".md": "text/markdown",
}


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


@router.get("/sources/{file_name:path}", response_class=FileResponse)
async def download_source(
    file_name: str,
    catalog: SourceCatalog = Depends(get_source_catalog),
) -> FileResponse:
    """Serve the original file (pdf, docx, xlsx…) behind an indexed document."""
    path = catalog.downloadable_path(file_name)
    if path is None:
        logger.info("Source download rejected: %s", file_name)
        raise HTTPException(status_code=404, detail="Документ не найден")

    media_type = EXTRA_MEDIA_TYPES.get(path.suffix.lower()) or (
        mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    disposition = "inline" if media_type in INLINE_MEDIA_TYPES else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type=disposition,
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )
