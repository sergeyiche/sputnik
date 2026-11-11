"""Session-scoped dialogue facts for the ``dialogue-memory`` context skill."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Whitelist only — no free-form medical dossier between sessions.
MEMORY_KEYS: dict[str, str] = {
    "patient_name": "Имя",
    "city": "Город",
    "meds": "Препараты",
    "caregiver": "С кем обращается",
    "preferred_time": "Удобное время",
    "condition_note": "Заметка о состоянии",
}

_NAME = re.compile(
    r"(?:меня\s+зовут|зовут\s+меня|зовите\s+меня|мое\s+имя|моё\s+имя)\s+([A-Za-zА-Яа-яЁё\-']{2,40})",
    re.IGNORECASE,
)
_CITY = re.compile(
    r"(?:живу\s+в(?:\s+городе)?|я\s+из(?:\s+города)?|город\s*[—\-:]?)\s+([A-Za-zА-Яа-яЁё\- ]{2,40})",
    re.IGNORECASE,
)
_MEDS = re.compile(
    r"(?:принимаю|пью|на\s+препарате)\s+([A-Za-zА-Яа-яЁё0-9\-]{3,40})"
    r"(?=\s*(?:\.|,|$|утром|вечером|днём|днем|ночью|каждый|по|\s+и\s))",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"(?:удобн\w+\s+(?:время|запис\w+)|запис\w+\s+(?:на|интересова\w+))\s+(утром|вечером|днём|днем|ночью)",
    re.IGNORECASE,
)
_CAREGIVER = re.compile(
    r"(?:я\s+(?:дочь|сын|внук|внучка|жена|муж|родственник\w*)|обращаюсь\s+за\s+(\w+))",
    re.IGNORECASE,
)

_EXTRACT_SYSTEM = (
    "Извлеки устойчивые факты о собеседнике из реплики пользователя. "
    "Верни только JSON-объект с известными ключами из списка: "
    + ", ".join(MEMORY_KEYS)
    + ". Значения — короткие строки на языке пользователя. "
    "Не выдумывай. Если фактов нет — верни {}."
)


@dataclass(frozen=True)
class MemoryFact:
    key: str
    value: str
    source: str
    confidence: float | None = None
    updated_at: str = ""


def format_memory_block(facts: list[MemoryFact], *, limit: int = 12) -> str:
    if not facts:
        return ""
    lines = ["## Известно о собеседнике", ""]
    for fact in facts[: max(1, limit)]:
        label = MEMORY_KEYS.get(fact.key, fact.key)
        lines.append(f"- {label}: {fact.value}")
    return "\n".join(lines).rstrip() + "\n"


def inject_memory_block(system_prompt: str, facts: list[MemoryFact], *, limit: int = 12) -> str:
    block = format_memory_block(facts, limit=limit)
    if not block:
        return system_prompt
    base = (system_prompt or "").rstrip()
    return f"{base}\n\n{block}" if base else block


def extract_facts_rules(user_text: str) -> list[tuple[str, str, float]]:
    """Return ``(key, value, confidence)`` from deterministic patterns."""
    text = user_text.strip()
    if not text:
        return []
    found: list[tuple[str, str, float]] = []

    match = _NAME.search(text)
    if match:
        found.append(("patient_name", _clean(match.group(1)), 0.9))

    match = _CITY.search(text)
    if match:
        found.append(("city", _clean(match.group(1)), 0.85))

    match = _MEDS.search(text)
    if match:
        med = _clean(match.group(1))
        if len(med) >= 3 and not _looks_like_noise_med(med):
            found.append(("meds", med, 0.8))

    match = _TIME.search(text)
    if match:
        found.append(("preferred_time", _clean(match.group(1)).lower(), 0.75))

    match = _CAREGIVER.search(text)
    if match:
        raw = match.group(0)
        found.append(("caregiver", _clean(raw), 0.7))

    return _dedupe(found)


def extract_facts_llm(llm: Any, user_text: str) -> list[tuple[str, str, float]]:
    text = user_text.strip()
    if llm is None or not text or len(text) < 8:
        return []
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = llm.invoke(
            [
                SystemMessage(content=_EXTRACT_SYSTEM),
                HumanMessage(content=text[:1500]),
            ]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        data = _parse_json_object(raw)
    except Exception:
        logger.debug("dialogue memory LLM extract failed", exc_info=True)
        return []

    found: list[tuple[str, str, float]] = []
    for key, value in data.items():
        if key not in MEMORY_KEYS:
            continue
        cleaned = _clean(str(value))
        if cleaned:
            found.append((key, cleaned, 0.7))
    return _dedupe(found)


def extract_facts(user_text: str, *, llm: Any | None = None, use_llm: bool = True) -> list[tuple[str, str, float]]:
    """Prefer fast rules; LLM only when the utterance looks personal and rules found nothing."""
    rules = extract_facts_rules(user_text)
    by_key = {key: (key, value, conf) for key, value, conf in rules}
    if use_llm and llm is not None and not by_key and _looks_personal(user_text):
        for key, value, conf in extract_facts_llm(llm, user_text):
            by_key[key] = (key, value, conf)
    return list(by_key.values())


def _looks_personal(text: str) -> bool:
    return (
        re.search(
            r"\b(меня|зовут|живу|город|принимаю|пью|дочь|сын|внук|внучка|жена|муж|удобн)\w*\b",
            text,
            re.IGNORECASE,
        )
        is not None
    )


def _clean(value: str) -> str:
    text = " ".join(value.strip().strip("\"'«».").split())
    return text[:120]


def _looks_like_noise_med(value: str) -> bool:
    lowered = value.lower()
    return lowered in {"это", "их", "его", "её", "ее", "то", "что", "препараты", "лекарства"}


def _dedupe(items: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    out: dict[str, tuple[str, str, float]] = {}
    for key, value, conf in items:
        prev = out.get(key)
        if prev is None or conf >= prev[2]:
            out[key] = (key, value, conf)
    return list(out.values())


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}
