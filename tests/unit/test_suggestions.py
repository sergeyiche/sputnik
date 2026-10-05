"""Unit tests for the starter suggestion catalog."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from packages.dialogue.suggestions import SuggestionCatalog, SuggestionCatalogError

PROJECT_CATALOG = Path(__file__).resolve().parents[2] / "config" / "starter_questions.json"


def _write_catalog(path: Path, questions: list[str]) -> Path:
    path.write_text(json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8")
    return path


def test_project_catalog_has_30_unique_questions() -> None:
    questions = SuggestionCatalog(path=PROJECT_CATALOG).questions()
    assert len(questions) == 30
    assert all(q.endswith("?") for q in questions)


def test_sample_returns_distinct_questions(tmp_path: Path) -> None:
    catalog = SuggestionCatalog(path=_write_catalog(tmp_path / "q.json", ["a?", "b?", "c?", "d?"]))
    sample = catalog.sample(3, rng=random.Random(42))
    assert len(sample) == 3
    assert len(set(sample)) == 3


def test_sample_caps_at_pool_size(tmp_path: Path) -> None:
    catalog = SuggestionCatalog(path=_write_catalog(tmp_path / "q.json", ["a?", "b?"]))
    assert sorted(catalog.sample(5)) == ["a?", "b?"]


def test_blank_and_duplicate_questions_are_dropped(tmp_path: Path) -> None:
    catalog = SuggestionCatalog(path=_write_catalog(tmp_path / "q.json", ["a?", " ", "a?", "b?"]))
    assert catalog.questions() == ["a?", "b?"]


def test_catalog_reloads_after_file_change(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "q.json", ["a?"])
    catalog = SuggestionCatalog(path=path)
    assert catalog.questions() == ["a?"]

    _write_catalog(path, ["b?"])
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert catalog.questions() == ["b?"]


def test_missing_catalog_raises(tmp_path: Path) -> None:
    with pytest.raises(SuggestionCatalogError):
        SuggestionCatalog(path=tmp_path / "missing.json").questions()
