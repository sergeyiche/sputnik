"""Persistent widget dialogues. Rows stay until an operator removes the database."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class DialogueMessage:
    role: str
    content: str
    sources: list[dict] = field(default_factory=list)
    created_at: str = ""


@dataclass
class Dialogue:
    session_id: str
    tenant_id: int | None
    topic: str
    created_at: str
    updated_at: str
    messages: list[DialogueMessage]


@dataclass
class DialogueSummary:
    session_id: str
    created_at: str
    updated_at: str
    preview: str
    message_count: int


@dataclass(frozen=True)
class DialogueMemory:
    key: str
    value: str
    source: str
    confidence: float | None = None
    updated_at: str = ""


class DialogueStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def get(self, session_id: str, *, tenant_id: int | None) -> Dialogue | None:
        clause, params = _tenant_sql(tenant_id)
        with self._lock, self._session() as conn:
            row = conn.execute(
                f"""
                SELECT id, tenant_id, topic, created_at, updated_at
                FROM chat_sessions
                WHERE id = ? AND {clause}
                """,
                (session_id, *params),
            ).fetchone()
            if row is None:
                return None
            messages = conn.execute(
                """
                SELECT role, content, sources_json, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        return _dialogue(row, messages)

    def record_exchange(
        self,
        session_id: str | None,
        *,
        tenant_id: int | None,
        user_text: str,
        answer_text: str,
        sources: list[dict] | None,
        topic: str,
    ) -> str:
        """Store both turns. Creates the session on the first message and returns its id."""
        with self._lock, self._session() as conn:
            sid = _usable_id(conn, session_id, tenant_id)
            now = _now()
            existing = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (sid,)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO chat_sessions (id, tenant_id, created_at, updated_at, topic)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (sid, tenant_id, now, now, topic),
                )
            else:
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ?, topic = ? WHERE id = ?",
                    (now, topic, sid),
                )
            conn.execute(
                """
                INSERT INTO chat_messages (session_id, tenant_id, role, content, sources_json, created_at)
                VALUES (?, ?, 'user', ?, '[]', ?)
                """,
                (sid, tenant_id, user_text, now),
            )
            conn.execute(
                """
                INSERT INTO chat_messages (session_id, tenant_id, role, content, sources_json, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)
                """,
                (sid, tenant_id, answer_text, _sources_json(sources), now),
            )
            return sid

    def list_sessions(
        self,
        *,
        tenant_id: int | None,
        limit: int = 30,
        before: str | None = None,
    ) -> tuple[list[DialogueSummary], str | None]:
        limit = max(1, min(limit, 100))
        clause, params = _tenant_sql(tenant_id, "s")
        sql = f"""
            SELECT s.id, s.created_at, s.updated_at,
                   (
                       SELECT m.content FROM chat_messages m
                       WHERE m.session_id = s.id AND m.role = 'user'
                       ORDER BY m.id LIMIT 1
                   ) AS preview,
                   (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
            FROM chat_sessions s
            WHERE {clause}
        """
        args: list[object] = list(params)
        cursor = _cursor(before)
        if cursor is not None:
            stamp, sid = cursor
            if sid:
                sql += " AND (s.updated_at < ? OR (s.updated_at = ? AND s.id < ?))"
                args.extend([stamp, stamp, sid])
            else:
                sql += " AND s.updated_at < ?"
                args.append(stamp)
        sql += " ORDER BY s.updated_at DESC, s.id DESC LIMIT ?"
        args.append(limit + 1)
        with self._lock, self._session() as conn:
            rows = conn.execute(sql, args).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        items = [
            DialogueSummary(
                session_id=row["id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                preview=_preview(row["preview"]),
                message_count=int(row["message_count"]),
            )
            for row in page
        ]
        next_before = f"{page[-1]['updated_at']}|{page[-1]['id']}" if has_more and page else None
        return items, next_before

    def list_memories(
        self,
        session_id: str,
        *,
        tenant_id: int | None,
        limit: int = 20,
    ) -> list[DialogueMemory]:
        limit = max(1, min(limit, 50))
        with self._lock, self._session() as conn:
            if not self._session_belongs(conn, session_id, tenant_id):
                return []
            rows = conn.execute(
                """
                SELECT key, value, source, confidence, updated_at
                FROM dialogue_memories
                WHERE session_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            DialogueMemory(
                key=row["key"],
                value=row["value"],
                source=row["source"],
                confidence=row["confidence"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_memory(
        self,
        session_id: str,
        *,
        tenant_id: int | None,
        key: str,
        value: str,
        source: str = "extracted",
        confidence: float | None = None,
    ) -> DialogueMemory | None:
        key = key.strip()
        value = " ".join(value.strip().split())
        if not session_id or not key or not value:
            return None
        now = _now()
        with self._lock, self._session() as conn:
            if not self._session_belongs(conn, session_id, tenant_id):
                # Session may be created in the same turn after answer — allow insert if absent.
                exists = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
                if exists is None:
                    return None
            conn.execute(
                """
                INSERT INTO dialogue_memories (
                    tenant_id, session_id, key, value, source, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, key) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at,
                    tenant_id = COALESCE(excluded.tenant_id, dialogue_memories.tenant_id)
                """,
                (tenant_id, session_id, key, value[:500], source, confidence, now),
            )
        return DialogueMemory(key=key, value=value[:500], source=source, confidence=confidence, updated_at=now)

    def _session_belongs(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        tenant_id: int | None,
    ) -> bool:
        row = conn.execute("SELECT tenant_id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return False
        return _same_tenant(row["tenant_id"], tenant_id)

    @contextmanager
    def _session(self):
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self._lock, self._session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    tenant_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_updated
                    ON chat_sessions (tenant_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES chat_sessions(id),
                    tenant_id INTEGER,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                    ON chat_messages (session_id, id);
                CREATE TABLE IF NOT EXISTS dialogue_memories (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER,
                    session_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (session_id, key)
                );
                CREATE INDEX IF NOT EXISTS idx_dialogue_memories_session
                    ON dialogue_memories (session_id, updated_at DESC);
                """
            )


def _tenant_sql(tenant_id: int | None, alias: str = "") -> tuple[str, tuple[object, ...]]:
    column = f"{alias}.tenant_id" if alias else "tenant_id"
    if tenant_id is None:
        return f"{column} IS NULL", ()
    return f"{column} = ?", (tenant_id,)


def _same_tenant(stored: int | None, requested: int | None) -> bool:
    return stored == requested


def _usable_id(conn: sqlite3.Connection, session_id: str | None, tenant_id: int | None) -> str:
    if session_id:
        row = conn.execute("SELECT tenant_id FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None or _same_tenant(row["tenant_id"], tenant_id):
            return session_id
    return str(uuid.uuid4())


def _dialogue(row: sqlite3.Row, messages: list[sqlite3.Row]) -> Dialogue:
    return Dialogue(
        session_id=row["id"],
        tenant_id=row["tenant_id"],
        topic=row["topic"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        messages=[
            DialogueMessage(
                role=item["role"],
                content=item["content"],
                sources=_load_sources(item["sources_json"]),
                created_at=item["created_at"],
            )
            for item in messages
        ],
    )


def _load_sources(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _sources_json(sources: list[dict] | None) -> str:
    clean: list[dict] = []
    for item in sources or []:
        if isinstance(item, dict):
            clean.append({key: value for key, value in item.items() if isinstance(key, str)})
    return json.dumps(clean, ensure_ascii=False)


def _cursor(before: str | None) -> tuple[str, str] | None:
    if not before:
        return None
    stamp, separator, sid = before.partition("|")
    if not separator or not stamp or not sid:
        return (before, "")
    return stamp, sid


def _preview(text: str | None) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "Без текста"
    if len(compact) <= 160:
        return compact
    return compact[:159].rstrip() + "…"


def _now() -> str:
    return datetime.now(UTC).isoformat()
