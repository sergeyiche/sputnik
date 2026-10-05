"""Unit tests for the background import runner."""

from __future__ import annotations

import threading

import pytest

from packages.admin.ingest_jobs import IngestAlreadyRunningError, IngestDependencies, IngestJobRunner
from packages.etl.ingest import PreparedChunks


class FakeStore:
    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log

    def delete_collection(self) -> None:
        self.log.append(f"delete:{self.name}")


def _prepared(count: int) -> PreparedChunks:
    return PreparedChunks(
        texts=[f"t{i}" for i in range(count)],
        metadatas=[{} for _ in range(count)],
        ids=[str(i) for i in range(count)],
        document_count=2,
    )


def _runner(log: list[str], prepared: PreparedChunks, gate: threading.Event | None = None) -> IngestJobRunner:
    def store_chunks(store, chunks, on_progress):
        if gate is not None:
            gate.wait(5)
        on_progress(len(chunks), len(chunks))
        log.append(f"store:{store.name}:{len(chunks)}")

    deps = IngestDependencies(
        prepare_chunks=lambda: prepared,
        create_store=lambda name: FakeStore(name, log),
        store_chunks=store_chunks,
        replace_collection=lambda staging, target: log.append(f"swap:{staging}->{target}"),
        on_success=lambda: log.append("reset"),
    )
    return IngestJobRunner(deps, collection_name="kb")


def test_successful_run_builds_staging_then_swaps() -> None:
    log: list[str] = []
    runner = _runner(log, _prepared(3))
    runner.start()
    runner.wait(5)

    state = runner.state
    assert state.status == "succeeded"
    assert (state.documents, state.chunks_total, state.chunks_done) == (2, 3, 3)
    assert log == ["delete:kb__staging", "store:kb__staging:3", "swap:kb__staging->kb", "reset"]


def test_empty_knowledge_fails_without_swap() -> None:
    log: list[str] = []
    runner = _runner(log, _prepared(0))
    runner.start()
    runner.wait(5)

    assert runner.state.status == "failed"
    assert "нет документов" in (runner.state.error or "")
    assert not any(entry.startswith("swap") for entry in log)


def test_second_start_while_running_is_rejected() -> None:
    gate = threading.Event()
    runner = _runner([], _prepared(1), gate)
    runner.start()
    with pytest.raises(IngestAlreadyRunningError):
        runner.start()
    gate.set()
    runner.wait(5)
    assert runner.state.status == "succeeded"
