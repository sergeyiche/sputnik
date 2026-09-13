"""Document ingestion: load files, chunk, embed, store in vector DB."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader

from packages.knowledge.embeddings_factory import create_embeddings
from packages.knowledge.factory import create_vector_store


def _load_documents(source_dir: Path):
    loaders = []
    for pattern, glob in [
        ("**/*.md", "**/*.md"),
        ("**/*.txt", "**/*.txt"),
    ]:
        loader = DirectoryLoader(
            str(source_dir),
            glob=glob,
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8"},
            show_progress=True,
            use_multithreading=True,
        )
        loaders.append(loader)

    docs = []
    for loader in loaders:
        try:
            docs.extend(loader.load())
        except Exception:
            continue
    return docs


def _chunk_id(source_name: str, index: int, content: str) -> str:
    payload = f"{source_name}:{index}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ingest(source_dir: str, recreate: bool = False) -> int:
    source = Path(source_dir)
    if not source.exists():
        raise FileNotFoundError(f"Knowledge directory not found: {source}")

    embeddings = create_embeddings()
    store = create_vector_store(embeddings)

    if recreate:
        store.delete_collection()
        store = create_vector_store(embeddings)

    documents = _load_documents(source)
    if not documents:
        print(f"No documents found in {source}")
        return 0

    chunk_size = int(os.getenv("RAG_CHUNK_SIZE", "800"))
    chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", "120"))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)

    texts: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for index, chunk in enumerate(chunks):
        source_name = chunk.metadata.get("source", "unknown")
        chunk_id = _chunk_id(source_name, index, chunk.page_content)
        texts.append(chunk.page_content)
        metadatas.append({
            "source": Path(source_name).name,
            "path": source_name,
            "format": Path(source_name).suffix.lstrip(".").lower() or "txt",
            "chunk_index": index,
        })
        ids.append(chunk_id)

    if len(ids) != len(set(ids)):
        raise ValueError("Internal error: duplicate chunk IDs generated")

    store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
    count = store.count()
    print(f"Ingested {len(chunks)} chunks from {len(documents)} documents. Total in store: {count}")
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge base into vector store")
    parser.add_argument("--source", default=os.getenv("KNOWLEDGE_DIR", "./data/knowledge"))
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate collection")
    args = parser.parse_args()
    ingest(args.source, recreate=args.recreate)


if __name__ == "__main__":
    main()
