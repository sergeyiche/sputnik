"""Document ingestion: load files, chunk, embed, store in vector DB."""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from packages.etl.convert import is_service_file
from packages.knowledge.base import VectorStore
from packages.knowledge.embeddings_factory import create_embeddings
from packages.knowledge.factory import create_vector_store

EMBED_BATCH_SIZE = 64

ProgressCallback = Callable[[int, int], None]


@dataclass
class PreparedChunks:
    texts: list[str] = field(default_factory=list)
    metadatas: list[dict] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    document_count: int = 0

    def __len__(self) -> int:
        return len(self.texts)


def _load_documents(source_dir: Path):
    docs = []
    for glob in ("**/*.md", "**/*.txt"):
        loader = DirectoryLoader(
            str(source_dir),
            glob=glob,
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"},
            show_progress=False,
            use_multithreading=True,
        )
        try:
            docs.extend(loader.load())
        except Exception:
            continue
    return [doc for doc in docs if not is_service_file(Path(doc.metadata.get("source", "")))]


def _chunk_id(source_name: str, index: int, content: str) -> str:
    payload = f"{source_name}:{index}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prepare_chunks(source_dir: str | Path) -> PreparedChunks:
    """Load knowledge documents and split them into chunks with stable IDs and metadata."""
    source = Path(source_dir)
    if not source.exists():
        raise FileNotFoundError(f"Knowledge directory not found: {source}")

    documents = _load_documents(source)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "800")),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "120")),
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)

    prepared = PreparedChunks(document_count=len(documents))
    for index, chunk in enumerate(chunks):
        source_name = chunk.metadata.get("source", "unknown")
        prepared.texts.append(chunk.page_content)
        prepared.metadatas.append({
            "source": Path(source_name).name,
            "path": source_name,
            "format": Path(source_name).suffix.lstrip(".").lower() or "txt",
            "chunk_index": index,
        })
        prepared.ids.append(_chunk_id(source_name, index, chunk.page_content))

    if len(prepared.ids) != len(set(prepared.ids)):
        raise ValueError("Internal error: duplicate chunk IDs generated")
    return prepared


def store_chunks(
    store: VectorStore,
    prepared: PreparedChunks,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Embed and add chunks in batches, reporting ``(done, total)`` after each batch."""
    total = len(prepared)
    for start in range(0, total, EMBED_BATCH_SIZE):
        end = min(start + EMBED_BATCH_SIZE, total)
        store.add_texts(
            texts=prepared.texts[start:end],
            metadatas=prepared.metadatas[start:end],
            ids=prepared.ids[start:end],
        )
        if on_progress is not None:
            on_progress(end, total)


def ingest(source_dir: str, recreate: bool = False) -> int:
    prepared = prepare_chunks(source_dir)
    if not prepared:
        print(f"No documents found in {source_dir}")
        return 0

    embeddings = create_embeddings()
    store = create_vector_store(embeddings)
    if recreate:
        store.delete_collection()
        store = create_vector_store(embeddings)

    store_chunks(store, prepared)
    print(
        f"Ingested {len(prepared)} chunks from {prepared.document_count} documents. "
        f"Total in store: {store.count()}"
    )
    return len(prepared)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge base into vector store")
    parser.add_argument("--source", default=os.getenv("KNOWLEDGE_DIR", "./data/knowledge"))
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    args = parser.parse_args()
    ingest(args.source, recreate=args.recreate)


if __name__ == "__main__":
    main()
