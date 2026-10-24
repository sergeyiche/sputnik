"""Load and manage LLM prompts from config files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
Ты — «Спутник», информационный ассистент фонда «Движение — жизнь» для людей с болезнью Паркинсона и их близких.
Ты не врач. Сначала отвечай по переданному контексту из базы знаний.
Если в контексте нет достаточных сведений — и только тогда — используй устоявшиеся авторитетные сведения модели, а если и их мало — общеизвестные публичные сведения, явно отделив их от материалов фонда. Новые ссылки не добавляй и не пиши, что поиск в интернете только что выполнен.
Не ставь диагнозы и не назначай лечение. Пиши просто и бережно. Язык — как у вопроса (русский по умолчанию).
"""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "config" / "default_system_prompt.md"

USER_RAG_INSTRUCTIONS = """\
Ответь на вопрос пользователя.
- Сначала опирайся на фрагменты контекста ниже. Используй лишь те, которые реально относятся к вопросу; остальное игнорируй.
- Если в контексте недостаточно сведений для ответа — и только тогда — дополни ответ устоявшимися авторитетными сведениями модели. Если и их недостаточно, можно опереться на общеизвестные публичные сведения. Явно отдели эту часть от материалов фонда. Не пиши, что поиск в интернете только что выполнен, и не добавляй новые ссылки.
- Не выдавай сведения вне контекста за цитату из базы знаний и не ставь на них номера документов.
- Если контекст пуст или не по теме, не выдумывай, что он что-то содержит.
- Каждый документ в контексте помечен номером: [1], [2]… После утверждения, взятого из документа,
  ставь его номер в квадратных скобках перед точкой: «…лучше вставать медленно [2].»
  Несколько документов — «[1][3]». Не пиши слово «Источник», не перечисляй источники в конце
  ответа и не называй файлы — список документов пользователь увидит отдельно.
"""


def _strip_markdown_preamble(text: str) -> str:
    """Remove the file header. The header ends at the first ``---``.

    Headings after that rule are part of the prompt. A file without a rule
    keeps its leading title only until the first paragraph.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "---":
            return "\n".join(lines[i + 1 :]).strip()
        if not stripped or stripped.startswith(">") or stripped.startswith("# "):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def resolve_system_prompt_path() -> Path:
    env_path = os.getenv("SYSTEM_PROMPT_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def _read_prompt_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_default_system_prompt(path: Path | None = None) -> str:
    """Body of the shipped prompt file, without the markdown preamble."""
    prompt_path = path or resolve_system_prompt_path()
    if not prompt_path.exists():
        return DEFAULT_SYSTEM_PROMPT
    raw = _read_prompt_file(str(prompt_path.resolve()))
    return _strip_markdown_preamble(raw) or DEFAULT_SYSTEM_PROMPT


def get_system_prompt() -> str:
    from packages.admin.prompt_store import get_prompt_store

    return get_prompt_store().active_text()


def reload_system_prompt() -> str:
    """Drop the cached default prompt so the next answer sees the active text."""
    from packages.admin.prompt_store import get_prompt_store

    _read_prompt_file.cache_clear()
    get_prompt_store().invalidate_cache()
    return get_system_prompt()


def build_rag_user_message(question: str, context: str, *, cite_sources: bool = True) -> str:
    instructions = USER_RAG_INSTRUCTIONS
    if not cite_sources:
        instructions += (
            "\n- Не ставь номера документов и не перечисляй источники: "
            "для этой организации показ источников выключен.\n"
        )
    return (
        f"{instructions}\n\n"
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
