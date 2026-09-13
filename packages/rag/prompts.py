"""Load and manage LLM prompts from config files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
Ты — информационный ассистент для людей с болезнью Паркинсона и их близких.

Правила:
1. Отвечай только на основе предоставленного контекста из базы знаний.
2. Если в контексте нет достаточной информации — честно скажи об этом и предложи обратиться к врачу.
3. Не ставь диагнозы, не назначай лечение и не меняй схему приёма препаратов.
4. Пиши понятным языком, без излишнего медицинского жаргона.
5. Отвечай на том языке, на котором задан вопрос (русский по умолчанию).
6. Если вопрос не связан с болезнью Паркинсона — вежливо перенаправь к теме.
"""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "config" / "system_prompt.md"


def _strip_markdown_preamble(text: str) -> str:
    """Remove YAML-style comment block and markdown heading at the top of prompt file."""
    lines = text.splitlines()
    body: list[str] = []
    in_comment = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped.startswith("# ") and not body:
            continue
        if stripped == "---" and not body:
            in_comment = not in_comment
            continue
        if in_comment:
            continue
        body.append(line)
    return "\n".join(body).strip()


def _resolve_prompt_path() -> Path:
    env_path = os.getenv("SYSTEM_PROMPT_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def _read_prompt_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def get_system_prompt() -> str:
    path = _resolve_prompt_path()
    if not path.exists():
        return DEFAULT_SYSTEM_PROMPT

    raw = _read_prompt_file(str(path.resolve()))
    cleaned = _strip_markdown_preamble(raw)
    return cleaned or DEFAULT_SYSTEM_PROMPT


def reload_system_prompt() -> str:
    """Clear cache after prompt file edit (for future admin hot-reload)."""
    _read_prompt_file.cache_clear()
    return get_system_prompt()
