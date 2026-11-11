"""Turn a short follow-up into a standalone search question."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from packages.integrations.gigachat.errors import is_gigachat_auth_error

FOLLOWUP_MAX_LEN = 80
_ELLIPSIS = re.compile(
    r"(?:^а\b)|(?:\b(?:это|почему|сколько)\b)",
    re.IGNORECASE,
)
_REWRITE_SYSTEM = (
    "Ты переписываешь реплику пользователя в один самостоятельный поисковый вопрос на языке реплики. "
    "Если реплика уточняет текущую тему диалога, включи предмет темы в вопрос. "
    "Если это новый самостоятельный вопрос, верни его без старой темы. "
    "Верни только одну строку вопроса, без кавычек и пояснений."
)


def is_followup(question: str) -> bool:
    """A short or elliptical utterance that depends on the dialogue so far."""
    text = question.strip()
    if not text:
        return False
    if len(text) < FOLLOWUP_MAX_LEN:
        return True
    return _ELLIPSIS.search(text) is not None


def fallback_standalone(topic: str, question: str) -> str:
    """Glue the saved topic to the current utterance. Do not use the previous short turn."""
    topic = topic.strip()
    question = question.strip()
    if len(topic) > 500:
        topic = topic[:500].rstrip()
    if not topic or topic == question:
        return question
    return f"{topic}\n{question}"


def resolve_turn(
    llm: Any | None,
    *,
    topic: str,
    history: Sequence[dict[str, str]] | None,
    question: str,
) -> tuple[str, str]:
    """Return ``(search_query, new_topic)``.

    The first message and a self-contained question keep the user's wording.
    A follow-up is rewritten into a standalone question. If the model is
    unavailable, the search query is the saved topic plus the current utterance,
    and the saved topic itself stays unchanged.
    """
    question = question.strip()
    turns = [msg for msg in (history or []) if (msg.get("content") or "").strip()]
    if not turns or not is_followup(question):
        return question, question

    anchor = topic.strip() or _first_user_message(turns)
    if llm is None:
        return fallback_standalone(anchor, question), anchor or question

    try:
        standalone = _rewrite_with_llm(llm, topic=anchor, history=turns, question=question)
    except Exception as exc:
        if is_gigachat_auth_error(exc):
            raise
        return fallback_standalone(anchor, question), anchor or question

    if standalone.lower() == question.lower():
        return fallback_standalone(anchor, question), anchor or question
    return standalone, standalone


def _first_user_message(history: Sequence[dict[str, str]]) -> str:
    for msg in history:
        if msg.get("role") == "user" and (msg.get("content") or "").strip():
            return msg["content"].strip()
    return ""


def _history_excerpt(history: Sequence[dict[str, str]], limit: int = 8) -> str:
    lines: list[str] = []
    for msg in list(history)[-limit:]:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 500:
            content = content[:500].rstrip() + "…"
        role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _rewrite_with_llm(
    llm: Any,
    *,
    topic: str,
    history: Sequence[dict[str, str]],
    question: str,
) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = (
        f"Тема диалога: {topic or 'не задана'}\n\n"
        f"Последние реплики:\n{_history_excerpt(history) or 'нет'}\n\n"
        f"Новая реплика: {question}"
    )
    response = llm.invoke([SystemMessage(content=_REWRITE_SYSTEM), HumanMessage(content=prompt)])
    raw = response.content if isinstance(response.content, str) else str(response.content)
    line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    line = line.strip("\"'«»").strip()
    if not line:
        raise ValueError("empty rewrite")
    return line[:500]
