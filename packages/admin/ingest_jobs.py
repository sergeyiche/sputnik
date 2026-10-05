"""Background full import (ingest --recreate) without downtime.

Chunks are written into a staging collection; only when it is complete does it
replace the live collection, so the assistant keeps answering from the old
index for the whole duration of the import.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from packages.etl.ingest import PreparedChunks, ProgressCallback
from packages.knowledge.base import VectorStore

logger = logging.getLogger(__name__)


class IngestAlreadyRunningError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestJobState:
    status: str = "idle"  # idle | running | succeeded | failed
    phase: str | None = None  # preparing | embedding | swapping
    started_at: datetime | None = None
    finished_at: datetime | None = None
    documents: int = 0
    chunks_total: int = 0
    chunks_done: int = 0
    duration_seconds: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestDependencies:
    prepare_chunks: Callable[[], PreparedChunks]
    create_store: Callable[[str], VectorStore]
    store_chunks: Callable[[VectorStore, PreparedChunks, ProgressCallback], None]
    replace_collection: Callable[[str, str], None]
    on_success: Callable[[], None]


class IngestJobRunner:
    def __init__(self, deps: IngestDependencies, collection_name: str) -> None:
        self._deps = deps
        self._collection = collection_name
        self._staging = f"{collection_name}__staging"
        self._state = IngestJobState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> IngestJobState:
        with self._lock:
            return self._state

    def start(self) -> IngestJobState:
        with self._lock:
            if self._state.status == "running":
                raise IngestAlreadyRunningError("Импорт уже выполняется")
            self._state = IngestJobState(
                status="running", phase="preparing", started_at=datetime.now(UTC)
            )
            self._thread = threading.Thread(target=self._run, name="ingest-job", daemon=True)
            self._thread.start()
            return self._state

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _update(self, **changes) -> None:
        with self._lock:
            self._state = replace(self._state, **changes)

    def _run(self) -> None:
        started = time.monotonic()
        try:
            prepared = self._deps.prepare_chunks()
            if not prepared:
                raise RuntimeError("В папке knowledge нет документов для импорта")
            self._update(phase="embedding", documents=prepared.document_count, chunks_total=len(prepared))
            logger.info("Ingest job: %d chunks from %d documents", len(prepared), prepared.document_count)

            staging = self._deps.create_store(self._staging)
            staging.delete_collection()
            staging = self._deps.create_store(self._staging)
            self._deps.store_chunks(staging, prepared, lambda done, _total: self._update(chunks_done=done))

            self._update(phase="swapping")
            self._deps.replace_collection(self._staging, self._collection)
            self._deps.on_success()

            duration = round(time.monotonic() - started, 1)
            self._update(status="succeeded", phase=None, finished_at=datetime.now(UTC), duration_seconds=duration)
            logger.info("Ingest job succeeded in %.1fs", duration)
        except Exception as exc:
            logger.exception("Ingest job failed")
            self._update(
                status="failed",
                finished_at=datetime.now(UTC),
                duration_seconds=round(time.monotonic() - started, 1),
                error=str(exc) or exc.__class__.__name__,
            )
