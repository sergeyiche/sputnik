"""Dialogue memory extract + store."""

from __future__ import annotations

from pathlib import Path

from packages.dialogue.memory import (
    extract_facts_rules,
    format_memory_block,
    inject_memory_block,
    MemoryFact,
)
from packages.dialogue.store import DialogueStore


def test_extract_name_city_meds() -> None:
    facts = extract_facts_rules("Меня зовут Анна, живу в Казани, принимаю леводопу утром.")
    by_key = {key: value for key, value, _ in facts}
    assert by_key["patient_name"] == "Анна"
    assert "Казан" in by_key["city"]
    assert "леводоп" in by_key["meds"].lower()


def test_format_and_inject_block() -> None:
    facts = [
        MemoryFact(key="patient_name", value="Иван", source="extracted"),
        MemoryFact(key="city", value="Тула", source="extracted"),
    ]
    block = format_memory_block(facts)
    assert "Известно о собеседнике" in block
    assert "Имя: Иван" in block
    merged = inject_memory_block("Базовый промпт.", facts)
    assert merged.startswith("Базовый промпт.")
    assert "Тула" in merged


def test_memory_upsert_and_list(tmp_path: Path) -> None:
    store = DialogueStore(tmp_path / "dialogue.db")
    sid = store.record_exchange(
        None,
        tenant_id=1,
        user_text="Меня зовут Олег",
        answer_text="Здравствуйте, Олег",
        sources=[],
        topic="знакомство",
    )
    assert store.upsert_memory(sid, tenant_id=1, key="patient_name", value="Олег", source="extracted")
    store.upsert_memory(sid, tenant_id=1, key="patient_name", value="Олег Петров", source="extracted")
    items = store.list_memories(sid, tenant_id=1)
    assert len(items) == 1
    assert items[0].value == "Олег Петров"
    assert store.list_memories(sid, tenant_id=2) == []
