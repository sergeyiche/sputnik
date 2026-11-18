"""Strip editorial instructions addressed to the AI from retrieved context.

Knowledge documents are sometimes prepared as prompts for an assistant
("Инструкция для ИИ-агента", "Как отвечать:", "Тексты для ИИ-агента").
Such lines are data for the RAG index, but they must not reach the model as
instructions: they can override the system prompt or conflict with it.
"""

from __future__ import annotations

# Lines that are wholly meta-instructions. Matching is case-insensitive.
_META_LINE_MARKERS = (
    "инструкция для ии",
    "тексты для ии",
    "для ии-агента",
    "для ии-консультанта",
    "в твоих ответах",
    "как отвечать:",
    "ключевые формулировки:",
    "призывы к донату",
)

# Role paragraphs are removed only at the start of a line.
_ROLE_PREFIXES = (
    "роль: ты",
    "твоя задача —",
    "твоя задача:",
    "важное предупреждение: ты не являешься врачом",
    "ты — доброжелательный",
)


def sanitize_context_text(text: str) -> str:
    """Return a context fragment without AI-directed meta instructions."""
    if not text:
        return ""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().casefold()
        if any(marker in stripped for marker in _META_LINE_MARKERS):
            continue
        if any(stripped.startswith(prefix) for prefix in _ROLE_PREFIXES):
            continue
        kept.append(line)

    # Collapse the blank gaps left by removed paragraphs.
    cleaned: list[str] = []
    blank = 0
    for line in kept:
        if not line.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        cleaned.append(line)
    return "\n".join(cleaned).strip()
