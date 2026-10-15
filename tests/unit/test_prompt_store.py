"""Versioned system prompt: history, rollback and the shipped default."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.admin.prompt_store import (
    PromptUnchangedError,
    PromptValidationError,
    SystemPromptStore,
    get_prompt_store,
    reset_prompt_store,
)
from packages.rag.prompts import _read_prompt_file, get_system_prompt, reload_system_prompt

DEFAULT_FILE = """# Заголовок

> служебная шапка

---

Текст по умолчанию
"""


def _store(tmp_path: Path) -> tuple[SystemPromptStore, Path]:
    default_path = tmp_path / "system_prompt.md"
    default_path.write_text(DEFAULT_FILE, encoding="utf-8")
    return SystemPromptStore(default_path, tmp_path / "prompts"), default_path


def test_save_rolls_back_and_restores_default_without_touching_the_file(tmp_path: Path) -> None:
    store, default_path = _store(tmp_path)
    original = default_path.read_text(encoding="utf-8")

    assert store.snapshot().mode == "default"
    assert store.active_text() == "Текст по умолчанию"

    with pytest.raises(PromptValidationError):
        store.save("   ")
    with pytest.raises(PromptUnchangedError):
        store.save("Текст по умолчанию")

    first = store.save("Первая правка", "заметка")
    assert first.mode == "version"
    assert first.content == "Первая правка"
    assert first.versions[0].note == "заметка"
    assert store.active_text() == "Первая правка"

    second = store.save("Вторая правка")
    assert second.active_version_id != first.active_version_id
    assert [item.content for item in second.versions] == ["Вторая правка", "Первая правка"]

    rolled = store.activate(first.active_version_id or "")
    assert rolled.content == "Первая правка"
    assert len(rolled.versions) == 2
    assert store.active_text() == "Первая правка"

    restored = store.restore_default()
    assert restored.mode == "default"
    assert restored.content == "Текст по умолчанию"
    assert len(restored.versions) == 2
    assert default_path.read_text(encoding="utf-8") == original


def test_missing_active_version_falls_back_to_default(tmp_path: Path) -> None:
    store, _default_path = _store(tmp_path)
    saved = store.save("Живая версия")
    version_id = saved.active_version_id or ""
    (tmp_path / "prompts" / "versions" / f"{version_id}.json").unlink()

    snapshot = store.snapshot()
    assert snapshot.mode == "default"
    assert snapshot.content == "Текст по умолчанию"
    state = (tmp_path / "prompts" / "state.json").read_text(encoding="utf-8")
    assert '"mode": "default"' in state


def test_get_system_prompt_follows_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_path = tmp_path / "system_prompt.md"
    default_path.write_text(DEFAULT_FILE, encoding="utf-8")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(default_path))
    monkeypatch.setenv("PROMPT_VERSIONS_DIR", str(tmp_path / "prompts"))
    reset_prompt_store()
    _read_prompt_file.cache_clear()
    try:
        assert get_system_prompt() == "Текст по умолчанию"
        get_prompt_store().save("Пользовательский промпт")
        assert reload_system_prompt() == "Пользовательский промпт"
        get_prompt_store().activate(get_prompt_store().snapshot().versions[0].id)
        assert get_system_prompt() == "Пользовательский промпт"
        get_prompt_store().restore_default()
        assert reload_system_prompt() == "Текст по умолчанию"
    finally:
        reset_prompt_store()
        _read_prompt_file.cache_clear()
