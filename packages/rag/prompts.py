"""Load and manage LLM prompts from config files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
Ты — «Спутник», информационный ассистент фонда «Движение — жизнь» для людей с болезнью Паркинсона и их близких.
Ты не врач. Отвечай только по переданному контексту из базы знаний; если данных мало — скажи об этом.
Не ставь диагнозы и не назначай лечение. Пиши просто и бережно. Язык — как у вопроса (русский по умолчанию).
"""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "config" / "system_prompt.md"

USER_RAG_INSTRUCTIONS = """\
Ответь на вопрос пользователя, опираясь только на фрагменты контекста ниже.
- Используй лишь те фрагменты, которые реально относятся к вопросу; остальное игнорируй.
- Если релевантных фактов недостаточно — честно скажи об этом и предложи обратиться к специалисту.
- Не добавляй факты, которых нет в контексте.
- Имена файлов-источников можно кратко упомянуть, если это помогает (без жаргона про «базу» и RAG).
"""


def _strip_markdown_preamble(text: str) -> str:
    """Remove leading title, blockquotes and horizontal rules before the prompt body."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if (
            not stripped
            or stripped.startswith(">")
            or stripped.startswith("# ")
            or stripped == "---"
        ):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


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


def build_rag_user_message(question: str, context: str) -> str:
    return (
        f"{USER_RAG_INSTRUCTIONS}\n\n"
        f"Контекст из базы знаний:\n\n{context}\n\n"
        f"Вопрос пользователя: {question}"
    )


def strip_trailing_disclaimer(text: str) -> str:
    """Remove medical disclaimer appendix from stored assistant turns (keeps history lean)."""
    markers = ("\n\n---\n", "\n---\n")
    cut = len(text)
    for marker in markers:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()
