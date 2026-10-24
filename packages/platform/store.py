"""SQLite registry of organizations. Document bodies stay on disk."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from packages.platform.emails import valid_email
from packages.platform.host import slug_available
from packages.platform.passwords import hash_password, verify_password

DEFAULT_DISCLAIMER = (
    "Этот ассистент предоставляет общую справочную информацию и не заменяет консультацию специалиста."
)
DEFAULT_TITLE = "Спутник"
DEFAULT_COLOR = "#fc7f03"
MAX_WIDGET_TITLE = 120
MAX_WIDGET_DESCRIPTION = 150
MAX_DISCLAIMER = 250
MAX_LEGAL_NOTES = 3000


class PlatformError(Exception):
    """User-facing registry error."""


class SlugTakenError(PlatformError):
    pass


class InvalidSlugError(PlatformError):
    pass


class EmailTakenError(PlatformError):
    pass


class TenantNotFoundError(PlatformError):
    pass


@dataclass(frozen=True)
class Tenant:
    id: int
    slug: str
    name: str
    status: str
    collection_name: str
    prompt_path: str
    created_at: str
    email: str


@dataclass(frozen=True)
class WidgetSettings:
    tenant_id: int
    title: str
    description: str
    primary_color: str
    show_sources: bool
    disclaimer: str
    legal_notes: str
    logo_name: str


@dataclass(frozen=True)
class IssuedReset:
    email: str
    slug: str
    tenant_id: int
    token: str


@dataclass(frozen=True)
class UsageDay:
    day: str
    questions: int
    tokens_in: int
    tokens_out: int
    errors: int
    empty_retrievals: int
    http_429: int
    http_402: int


class PlatformStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def register(
        self,
        *,
        slug: str,
        name: str,
        email: str,
        password: str,
        widget_title: str = "",
        widget_description: str = "",
    ) -> Tenant:
        slug = slug.strip().lower()
        email = email.strip().lower()
        name = name.strip()
        if not slug_available(slug):
            raise InvalidSlugError("Такой адрес поддомена использовать нельзя")
        if not name or len(name) > 200:
            raise PlatformError("Укажите название организации")
        if not valid_email(email):
            raise PlatformError("Укажите корректный email")
        if len(password) < 8:
            raise PlatformError("Пароль должен быть не короче 8 символов")
        widget_title = widget_title.strip() or DEFAULT_TITLE
        widget_description = widget_description.strip()
        if len(widget_title) > MAX_WIDGET_TITLE:
            raise PlatformError("Название виджета длиннее 120 символов")
        if len(widget_description) > MAX_WIDGET_DESCRIPTION:
            raise PlatformError("Описание виджета длиннее 150 символов")
        now = _now()
        with self._lock, self._session() as conn:
            if conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
                raise SlugTakenError("Этот поддомен уже занят")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise EmailTakenError("Этот email уже зарегистрирован")
            cursor = conn.execute(
                """
                INSERT INTO tenants (slug, name, status, collection_name, prompt_path, created_at)
                VALUES (?, ?, 'pending', ?, '', ?)
                """,
                (slug, name, f"tenant_{slug}", now),
            )
            tenant_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO users (tenant_id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, email, hash_password(password), now),
            )
            self._insert_default_settings(
                conn,
                tenant_id,
                title=widget_title,
                description=widget_description,
            )
        tenant = self.get_by_slug(slug)
        if tenant is None:
            raise PlatformError("Не удалось создать заявку")
        return tenant

    def authenticate(self, email: str, password: str) -> Tenant | None:
        email = email.strip().lower()
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT tenant_id, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return self.get(int(row["tenant_id"]))

    def issue_password_reset(self, email: str, *, tenant_id: int, ttl_seconds: int) -> IssuedReset | None:
        """Create a one-time reset token for this organization's owner.

        Returns None when the mailbox is unknown or belongs to another organization.
        """
        email = email.strip().lower()
        if not valid_email(email):
            return None
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        now = _now()
        with self._lock, self._session() as conn:
            row = conn.execute(
                """
                SELECT u.id AS user_id, t.id AS tenant_id, t.slug
                FROM users u
                JOIN tenants t ON t.id = u.tenant_id
                WHERE u.email = ?
                """,
                (email,),
            ).fetchone()
            if row is None or int(row["tenant_id"]) != tenant_id:
                return None
            user_id = int(row["user_id"])
            conn.execute(
                "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                (now, user_id),
            )
            conn.execute(
                "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (user_id, _token_hash(token), expires),
            )
            issued = IssuedReset(
                email=email,
                slug=row["slug"],
                tenant_id=int(row["tenant_id"]),
                token=token,
            )
        return issued

    def reset_password(self, token: str, password: str) -> Tenant:
        if len(password) < 8 or len(password) > 256:
            raise PlatformError("Пароль должен быть не короче 8 символов")
        token = token.strip()
        if not token:
            raise PlatformError("Ссылка недействительна или устарела")
        now = datetime.now(UTC)
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT id, user_id, expires_at, used_at FROM password_resets WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None or row["used_at"]:
                raise PlatformError("Ссылка недействительна или устарела")
            try:
                expires = datetime.fromisoformat(row["expires_at"])
            except ValueError as exc:
                raise PlatformError("Ссылка недействительна или устарела") from exc
            if expires < now:
                raise PlatformError("Ссылка недействительна или устарела")
            user = conn.execute("SELECT tenant_id FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            if user is None:
                raise PlatformError("Ссылка недействительна или устарела")
            stamp = now.isoformat()
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), row["user_id"]),
            )
            conn.execute(
                "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                (stamp, row["user_id"]),
            )
            tenant_id = int(user["tenant_id"])
        tenant = self.get(tenant_id)
        if tenant is None:
            raise PlatformError("Ссылка недействительна или устарела")
        return tenant

    def get(self, tenant_id: int) -> Tenant | None:
        with self._lock, self._session() as conn:
            row = conn.execute(_TENANT_SQL + " WHERE t.id = ?", (tenant_id,)).fetchone()
        return _tenant(row) if row else None

    def get_by_slug(self, slug: str) -> Tenant | None:
        with self._lock, self._session() as conn:
            row = conn.execute(_TENANT_SQL + " WHERE t.slug = ?", (slug,)).fetchone()
        return _tenant(row) if row else None

    def list_tenants(self) -> list[Tenant]:
        with self._lock, self._session() as conn:
            rows = conn.execute(_TENANT_SQL + " ORDER BY t.created_at DESC").fetchall()
        return [_tenant(row) for row in rows]

    def ensure_active(
        self,
        *,
        slug: str,
        name: str,
        email: str,
        password: str,
        collection_name: str,
        prompt_path: str,
    ) -> Tenant:
        """Create an already-enabled organization. Used for the fund and help seeds."""
        existing = self.get_by_slug(slug)
        if existing is not None:
            return existing
        owner_email = email.strip().lower()
        if not valid_email(owner_email):
            raise PlatformError("Укажите корректный email")
        now = _now()
        with self._lock, self._session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tenants (slug, name, status, collection_name, prompt_path, created_at)
                VALUES (?, ?, 'active', ?, ?, ?)
                """,
                (slug, name, collection_name, prompt_path, now),
            )
            tenant_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO users (tenant_id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, owner_email, hash_password(password), now),
            )
            self._insert_default_settings(conn, tenant_id)
        tenant = self.get_by_slug(slug)
        if tenant is None:
            raise PlatformError("Не удалось создать организацию")
        return tenant

    def rename_slug(self, old_slug: str, new_slug: str) -> Tenant | None:
        """Rename an organization. Owner email and password stay in the database."""
        if self.get_by_slug(new_slug) is not None:
            return self.get_by_slug(new_slug)
        current = self.get_by_slug(old_slug)
        if current is None:
            return None
        with self._lock, self._session() as conn:
            conn.execute("UPDATE tenants SET slug = ? WHERE id = ?", (new_slug, current.id))
        return self.get_by_slug(new_slug)

    def approve(self, tenant_id: int, prompt_path: str) -> Tenant:
        return self._set_status(tenant_id, "active", prompt_path=prompt_path)

    def reject(self, tenant_id: int) -> Tenant:
        return self._set_status(tenant_id, "rejected")

    def set_enabled(self, tenant_id: int, enabled: bool) -> Tenant:
        tenant = self.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError("Организация не найдена")
        if tenant.status in {"pending", "rejected"}:
            raise PlatformError("Сначала подтвердите заявку")
        return self._set_status(tenant_id, "active" if enabled else "suspended")

    def settings_for(self, tenant_id: int) -> WidgetSettings:
        with self._lock, self._session() as conn:
            row = conn.execute("SELECT * FROM widget_settings WHERE tenant_id = ?", (tenant_id,)).fetchone()
        if row is None:
            raise TenantNotFoundError("Настройки виджета не найдены")
        return WidgetSettings(
            tenant_id=tenant_id,
            title=row["title"],
            description=row["description"],
            primary_color=row["primary_color"],
            show_sources=bool(row["show_sources"]),
            disclaimer=row["disclaimer"],
            legal_notes=row["legal_notes"],
            logo_name=row["logo_name"],
        )

    def update_settings(
        self,
        tenant_id: int,
        *,
        title: str,
        description: str,
        primary_color: str,
        show_sources: bool,
        disclaimer: str,
        legal_notes: str,
        logo_name: str | None = None,
    ) -> WidgetSettings:
        title = title.strip()
        description = description.strip()
        disclaimer = disclaimer.strip()
        legal_notes = legal_notes.strip()
        if not title:
            raise PlatformError("Укажите заголовок виджета")
        if len(title) > MAX_WIDGET_TITLE:
            raise PlatformError("Название виджета длиннее 120 символов")
        if len(description) > MAX_WIDGET_DESCRIPTION:
            raise PlatformError("Описание виджета длиннее 150 символов")
        if not disclaimer:
            raise PlatformError("Укажите юридическую информацию")
        if len(disclaimer) > MAX_DISCLAIMER:
            raise PlatformError("Юридическая информация длиннее 250 символов")
        if len(legal_notes) > MAX_LEGAL_NOTES:
            raise PlatformError("Подробная юридическая информация длиннее 3000 символов")
        if not primary_color.startswith("#") or len(primary_color) not in {4, 7}:
            raise PlatformError("Цвет укажите в виде #fc7f03")
        with self._lock, self._session() as conn:
            fields = [
                title,
                description,
                primary_color,
                int(show_sources),
                disclaimer,
                legal_notes,
            ]
            sql = """
                UPDATE widget_settings
                SET title = ?, description = ?, primary_color = ?, show_sources = ?,
                    disclaimer = ?, legal_notes = ?
            """
            if logo_name is not None:
                sql += ", logo_name = ?"
                fields.append(logo_name)
            sql += " WHERE tenant_id = ?"
            fields.append(tenant_id)
            updated = conn.execute(sql, fields).rowcount
        if not updated:
            raise TenantNotFoundError("Настройки виджета не найдены")
        return self.settings_for(tenant_id)

    def record_usage(
        self,
        tenant_id: int,
        *,
        questions: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        errors: int = 0,
        empty_retrievals: int = 0,
        http_429: int = 0,
        http_402: int = 0,
        day: date | None = None,
    ) -> None:
        stamp = (day or datetime.now(UTC).date()).isoformat()
        with self._lock, self._session() as conn:
            conn.execute(
                """
                INSERT INTO usage_daily (
                    tenant_id, day, questions, tokens_in, tokens_out, errors,
                    empty_retrievals, http_429, http_402
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, day) DO UPDATE SET
                    questions = questions + excluded.questions,
                    tokens_in = tokens_in + excluded.tokens_in,
                    tokens_out = tokens_out + excluded.tokens_out,
                    errors = errors + excluded.errors,
                    empty_retrievals = empty_retrievals + excluded.empty_retrievals,
                    http_429 = http_429 + excluded.http_429,
                    http_402 = http_402 + excluded.http_402
                """,
                (
                    tenant_id,
                    stamp,
                    questions,
                    tokens_in,
                    tokens_out,
                    errors,
                    empty_retrievals,
                    http_429,
                    http_402,
                ),
            )

    def usage_between(self, tenant_id: int, start: date, end: date) -> list[UsageDay]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM usage_daily
                WHERE tenant_id = ? AND day >= ? AND day <= ?
                ORDER BY day
                """,
                (tenant_id, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [
            UsageDay(
                day=row["day"],
                questions=row["questions"],
                tokens_in=row["tokens_in"],
                tokens_out=row["tokens_out"],
                errors=row["errors"],
                empty_retrievals=row["empty_retrievals"],
                http_429=row["http_429"],
                http_402=row["http_402"],
            )
            for row in rows
        ]

    def recent_429(self, *, hours: int = 1) -> int:
        """429 counters are daily, so the alert uses today's count as the recent signal."""
        today = datetime.now(UTC).date()
        if hours >= 24:
            start = today - timedelta(days=1)
        else:
            start = today
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(http_429), 0) AS total FROM usage_daily WHERE day >= ?",
                (start.isoformat(),),
            ).fetchone()
        return int(row["total"])

    def _set_status(self, tenant_id: int, status: str, prompt_path: str | None = None) -> Tenant:
        with self._lock, self._session() as conn:
            if prompt_path is None:
                updated = conn.execute(
                    "UPDATE tenants SET status = ? WHERE id = ?",
                    (status, tenant_id),
                ).rowcount
            else:
                updated = conn.execute(
                    "UPDATE tenants SET status = ?, prompt_path = ? WHERE id = ?",
                    (status, prompt_path, tenant_id),
                ).rowcount
        if not updated:
            raise TenantNotFoundError("Организация не найдена")
        tenant = self.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError("Организация не найдена")
        return tenant

    def _insert_default_settings(
        self,
        conn: sqlite3.Connection,
        tenant_id: int,
        *,
        title: str = DEFAULT_TITLE,
        description: str = "Ответы по базе знаний организации",
    ) -> None:
        conn.execute(
            """
            INSERT INTO widget_settings (
                tenant_id, title, description, primary_color, show_sources, disclaimer, legal_notes, logo_name
            ) VALUES (?, ?, ?, ?, 1, ?, '', '')
            """,
            (tenant_id, title, description, DEFAULT_COLOR, DEFAULT_DISCLAIMER),
        )

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
                CREATE TABLE IF NOT EXISTS tenants (
                    id INTEGER PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    collection_name TEXT NOT NULL,
                    prompt_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS widget_settings (
                    tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    primary_color TEXT NOT NULL,
                    show_sources INTEGER NOT NULL,
                    disclaimer TEXT NOT NULL,
                    legal_notes TEXT NOT NULL,
                    logo_name TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    day TEXT NOT NULL,
                    questions INTEGER NOT NULL DEFAULT 0,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0,
                    empty_retrievals INTEGER NOT NULL DEFAULT 0,
                    http_429 INTEGER NOT NULL DEFAULT 0,
                    http_402 INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, day)
                );
                CREATE TABLE IF NOT EXISTS password_resets (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                """
            )
            self._backfill_emails(conn)

    def _backfill_emails(self, conn: sqlite3.Connection) -> None:
        """Fill owners that have no real mailbox. The address is required."""
        rows = conn.execute(
            """
            SELECT u.id AS user_id, u.email, t.slug
            FROM users u
            JOIN tenants t ON t.id = u.tenant_id
            """
        ).fetchall()
        taken = {(row["email"] or "").strip().lower() for row in rows if valid_email(row["email"] or "")}
        for row in rows:
            current = (row["email"] or "").strip().lower()
            if valid_email(current):
                continue
            email = f"{row['slug']}@spootnik.local"
            if email in taken:
                email = f"{row['slug']}-{row['user_id']}@spootnik.local"
            taken.add(email)
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, row["user_id"]))


_TENANT_SQL = """
    SELECT t.id, t.slug, t.name, t.status, t.collection_name, t.prompt_path, t.created_at, u.email
    FROM tenants t
    JOIN users u ON u.tenant_id = t.id
"""


def _tenant(row: sqlite3.Row) -> Tenant:
    return Tenant(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        status=row["status"],
        collection_name=row["collection_name"],
        prompt_path=row["prompt_path"],
        created_at=row["created_at"],
        email=row["email"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
