"""In-memory storage for GigaChat access tokens."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock


@dataclass
class StoredToken:
    access_token: str
    expires_at: int  # Unix timestamp (seconds)

    def is_valid(self, buffer_seconds: int = 60) -> bool:
        return time.time() < (self.expires_at - buffer_seconds)


class GigaChatTokenStore:
    """Thread-safe singleton token cache."""

    _instance: GigaChatTokenStore | None = None
    _lock = Lock()

    def __new__(cls) -> GigaChatTokenStore:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._token: StoredToken | None = None
                    cls._instance._store_lock = Lock()
        return cls._instance

    def get(self) -> StoredToken | None:
        with self._store_lock:
            if self._token and self._token.is_valid():
                return self._token
            return None

    def save(self, access_token: str, expires_at: int) -> StoredToken:
        stored = StoredToken(access_token=access_token, expires_at=expires_at)
        with self._store_lock:
            self._token = stored
        return stored

    def clear(self) -> None:
        with self._store_lock:
            self._token = None


def get_token_store() -> GigaChatTokenStore:
    return GigaChatTokenStore()
