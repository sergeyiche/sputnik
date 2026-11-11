"""Widget dialogues stay in SQLite and are invisible to another organization."""

from __future__ import annotations

from pathlib import Path

from packages.dialogue.store import DialogueStore


def test_exchange_survives_reopen_and_hides_from_another_tenant(tmp_path: Path) -> None:
    path = tmp_path / "platform" / "platform.db"
    store = DialogueStore(path)
    session_id = store.record_exchange(
        "11111111-1111-4111-8111-111111111111",
        tenant_id=1,
        user_text="Расскажи про леводопу",
        answer_text="Леводопа — основной препарат.",
        sources=[{"index": 1, "title": "Препараты", "url": None}],
        topic="Расскажи про леводопу",
    )

    reopened = DialogueStore(path)
    loaded = reopened.get(session_id, tenant_id=1)
    assert loaded is not None
    assert loaded.topic == "Расскажи про леводопу"
    assert [item.role for item in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[0].content == "Расскажи про леводопу"
    assert loaded.messages[1].sources[0]["title"] == "Препараты"

    assert reopened.get(session_id, tenant_id=2) is None
    assert reopened.get(session_id, tenant_id=None) is None
    foreign, _ = reopened.list_sessions(tenant_id=2)
    assert foreign == []

    stolen = reopened.record_exchange(
        session_id,
        tenant_id=2,
        user_text="Чужой вопрос",
        answer_text="Не должен попасть в первый диалог",
        sources=[],
        topic="Чужой вопрос",
    )
    assert stolen != session_id
    again = reopened.get(session_id, tenant_id=1)
    assert again is not None
    assert [item.content for item in again.messages] == [
        "Расскажи про леводопу",
        "Леводопа — основной препарат.",
    ]


def test_list_pages_newest_first(tmp_path: Path) -> None:
    store = DialogueStore(tmp_path / "platform.db")
    first = store.record_exchange(None, tenant_id=None, user_text="Первый", answer_text="а", sources=[], topic="Первый")
    second = store.record_exchange(None, tenant_id=None, user_text="Второй", answer_text="б", sources=[], topic="Второй")
    page, cursor = store.list_sessions(tenant_id=None, limit=1)
    assert [item.session_id for item in page] == [second]
    assert cursor
    rest, end = store.list_sessions(tenant_id=None, limit=1, before=cursor)
    assert [item.session_id for item in rest] == [first]
    assert end is None
    assert rest[0].preview == "Первый"
    assert rest[0].message_count == 2
