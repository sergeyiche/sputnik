"""In-memory session store for anonymous chat (cookie-based session_id)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    messages: list[dict[str, str]] = field(default_factory=list)


class SessionStore:
    def __init__(self, max_messages: int = 20) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_messages = max_messages

    def get_or_create(self, session_id: str | None, *, namespace: str = "") -> Session:
        if session_id:
            found = self._sessions.get(self._key(namespace, session_id))
            if found is not None:
                return found

        new_id = session_id or str(uuid.uuid4())
        session = Session(session_id=new_id)
        self._sessions[self._key(namespace, new_id)] = session
        return session

    def add_message(self, session_id: str, role: str, content: str, *, namespace: str = "") -> None:
        session = self._sessions.get(self._key(namespace, session_id))
        if not session:
            return
        session.messages.append({"role": role, "content": content})
        if len(session.messages) > self._max_messages:
            session.messages = session.messages[-self._max_messages :]

    def get_history(self, session_id: str, *, namespace: str = "") -> list[dict[str, str]]:
        session = self._sessions.get(self._key(namespace, session_id))
        return list(session.messages) if session else []

    def clear(self, session_id: str, *, namespace: str = "") -> None:
        self._sessions.pop(self._key(namespace, session_id), None)

    @staticmethod
    def _key(namespace: str, session_id: str) -> str:
        return f"{namespace}:{session_id}" if namespace else session_id
