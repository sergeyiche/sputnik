"""Versioned system prompt. The shipped file stays the default and is never overwritten."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.rag.prompts import PROJECT_ROOT, read_default_system_prompt

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 20_000
MAX_NOTE_CHARS = 200
_VERSION_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")


class PromptStoreError(Exception):
    """Base error with a user-facing message."""


class PromptValidationError(PromptStoreError):
    pass


class PromptUnchangedError(PromptStoreError):
    pass


class PromptVersionNotFoundError(PromptStoreError):
    pass


@dataclass(frozen=True)
class PromptVersion:
    id: str
    created_at: datetime
    note: str
    content: str


@dataclass(frozen=True)
class PromptSnapshot:
    content: str
    mode: str
    active_version_id: str | None
    default_content: str
    versions: list[PromptVersion]


def resolve_versions_dir() -> Path:
    env_path = os.getenv("PROMPT_VERSIONS_DIR")
    if env_path:
        return Path(env_path)
    return PROJECT_ROOT / "data" / "prompts"


class SystemPromptStore:
    def __init__(self, default_path: Path, versions_dir: Path) -> None:
        self._default_path = default_path
        self._dir = versions_dir
        self._lock = threading.RLock()
        self._default_cache: str | None = None

    def active_text(self) -> str:
        with self._lock:
            mode, content, _version_id = self._resolve()
            return content if mode == "version" else self._default_body()

    def snapshot(self) -> PromptSnapshot:
        with self._lock:
            return self._snapshot()

    def save(self, content: str, note: str = "") -> PromptSnapshot:
        text = content.strip()
        note_text = note.strip()
        self._validate(text, note_text)
        with self._lock:
            _mode, current, _version_id = self._resolve()
            if text == current:
                raise PromptUnchangedError("Текст не изменился")
            version_id = _new_version_id()
            created_at = datetime.now(UTC)
            record = {
                "id": version_id,
                "created_at": created_at.isoformat(),
                "note": note_text,
                "content": text,
            }
            self._versions_dir().mkdir(parents=True, exist_ok=True)
            self._atomic_write_json(self._version_file(version_id), record)
            self._atomic_write_json(self._state_file(), {"mode": "version", "version_id": version_id})
            logger.info("Saved system prompt version %s", version_id)
            return self._snapshot()

    def activate(self, version_id: str) -> PromptSnapshot:
        if not _VERSION_ID.fullmatch(version_id):
            raise PromptVersionNotFoundError("Версия не найдена")
        with self._lock:
            if self._read_version(version_id) is None:
                raise PromptVersionNotFoundError("Версия не найдена")
            self._atomic_write_json(self._state_file(), {"mode": "version", "version_id": version_id})
            logger.info("Activated system prompt version %s", version_id)
            return self._snapshot()

    def restore_default(self) -> PromptSnapshot:
        with self._lock:
            self._default_cache = None
            self._atomic_write_json(self._state_file(), {"mode": "default"})
            logger.info("Restored default system prompt")
            return self._snapshot()

    def invalidate_cache(self) -> None:
        with self._lock:
            self._default_cache = None

    def _validate(self, text: str, note: str) -> None:
        if not text:
            raise PromptValidationError("Промпт не может быть пустым")
        if len(text) > MAX_PROMPT_CHARS:
            raise PromptValidationError(f"Промпт длиннее {MAX_PROMPT_CHARS} символов")
        if len(note) > MAX_NOTE_CHARS:
            raise PromptValidationError(f"Комментарий длиннее {MAX_NOTE_CHARS} символов")

    def _snapshot(self) -> PromptSnapshot:
        mode, content, version_id = self._resolve()
        versions = self._load_versions()
        return PromptSnapshot(
            content=content,
            mode=mode,
            active_version_id=version_id,
            default_content=self._default_body(),
            versions=versions,
        )

    def _resolve(self) -> tuple[str, str, str | None]:
        """Return mode, active content and version id. Caller holds the lock."""
        state = self._read_state()
        if state.get("mode") == "version":
            version_id = str(state.get("version_id") or "")
            version = self._read_version(version_id)
            if version is not None:
                return "version", version.content, version.id
            logger.error("Active prompt version %s is missing; falling back to default", version_id)
            self._atomic_write_json(self._state_file(), {"mode": "default"})
        return "default", self._default_body(), None

    def _default_body(self) -> str:
        if self._default_cache is None:
            self._default_cache = read_default_system_prompt(self._default_path)
        return self._default_cache

    def _state_file(self) -> Path:
        return self._dir / "state.json"

    def _versions_dir(self) -> Path:
        return self._dir / "versions"

    def _version_file(self, version_id: str) -> Path:
        return self._versions_dir() / f"{version_id}.json"

    def _read_state(self) -> dict:
        path = self._state_file()
        if not path.exists():
            return {"mode": "default"}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Cannot read prompt state %s; using default", path)
            return {"mode": "default"}
        if not isinstance(payload, dict) or payload.get("mode") not in {"default", "version"}:
            logger.error("Invalid prompt state in %s; using default", path)
            return {"mode": "default"}
        return payload

    def _read_version(self, version_id: str) -> PromptVersion | None:
        if not _VERSION_ID.fullmatch(version_id):
            return None
        path = self._version_file(version_id)
        if not path.exists():
            return None
        try:
            return _parse_version(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, PromptValidationError, ValueError):
            logger.exception("Cannot read prompt version %s", path)
            return None

    def _load_versions(self) -> list[PromptVersion]:
        directory = self._versions_dir()
        if not directory.exists():
            return []
        versions: list[PromptVersion] = []
        for path in directory.glob("*.json"):
            try:
                version = _parse_version(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, PromptValidationError, ValueError):
                logger.exception("Skipping unreadable prompt version %s", path)
                continue
            if version.id != path.stem:
                logger.error("Prompt version id does not match filename %s", path)
                continue
            versions.append(version)
        versions.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return versions

    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        temporary = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _new_version_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def _parse_version(payload: object) -> PromptVersion:
    if not isinstance(payload, dict):
        raise PromptValidationError("Версия промпта повреждена")
    version_id = str(payload.get("id") or "")
    if not _VERSION_ID.fullmatch(version_id):
        raise PromptValidationError("Некорректный идентификатор версии")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise PromptValidationError("В версии нет текста промпта")
    note = payload.get("note") or ""
    if not isinstance(note, str):
        raise PromptValidationError("Некорректный комментарий версии")
    created_raw = str(payload.get("created_at") or "")
    created_at = datetime.fromisoformat(created_raw)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return PromptVersion(id=version_id, created_at=created_at, note=note, content=content)


_store: SystemPromptStore | None = None
_store_key: tuple[str, str] | None = None


def get_prompt_store() -> SystemPromptStore:
    from packages.rag.prompts import resolve_system_prompt_path

    global _store, _store_key
    default_path = resolve_system_prompt_path()
    versions_dir = resolve_versions_dir()
    key = (str(default_path), str(versions_dir))
    if _store is None or _store_key != key:
        _store = SystemPromptStore(default_path, versions_dir)
        _store_key = key
    return _store


def reset_prompt_store() -> None:
    """Drop the process-wide store. Used by tests that change prompt paths."""
    global _store, _store_key
    _store = None
    _store_key = None
