"""Starter question suggestions loaded from a JSON catalog."""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = "./config/starter_questions.json"


class SuggestionCatalogError(RuntimeError):
    """Raised when the suggestion catalog cannot be loaded."""


@dataclass
class SuggestionCatalog:
    """Question catalog that reloads automatically when the file changes."""

    path: Path
    _questions: list[str] = field(default_factory=list, init=False)
    _mtime: float | None = field(default=None, init=False)

    def questions(self) -> list[str]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError as exc:
            raise SuggestionCatalogError(f"Suggestion catalog not found: {self.path}") from exc

        if self._mtime != mtime:
            self._questions = _parse_catalog(self.path)
            self._mtime = mtime
            logger.info("Loaded %d starter questions from %s", len(self._questions), self.path)
        return list(self._questions)

    def sample(self, count: int, rng: random.Random | None = None) -> list[str]:
        pool = self.questions()
        if count <= 0 or not pool:
            return []
        return (rng or random).sample(pool, min(count, len(pool)))


def _parse_catalog(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuggestionCatalogError(f"Cannot read suggestion catalog {path}: {exc}") from exc

    raw = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise SuggestionCatalogError(f"'questions' must be a list in {path}")

    seen: set[str] = set()
    questions: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            questions.append(text)
    return questions


_catalog: SuggestionCatalog | None = None


def get_starter_catalog() -> SuggestionCatalog:
    global _catalog
    path = Path(os.getenv("STARTER_QUESTIONS_PATH", DEFAULT_CATALOG_PATH))
    if _catalog is None or _catalog.path != path:
        _catalog = SuggestionCatalog(path=path)
    return _catalog
