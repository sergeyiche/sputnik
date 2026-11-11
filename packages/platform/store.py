"""SQLite registry of organizations. Document bodies stay on disk."""

from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from packages.platform.emails import valid_email
from packages.platform.host import slug_available
from packages.platform.passwords import hash_password, verify_password
from packages.platform.phones import format_phone, normalize_phone
from packages.skills.models import ACTIVATIONS, NAME_PATTERN, SKILL_TYPES, Skill, SkillExample
from packages.skills.seeds import PARKINSON_SKILL_SEEDS

DEFAULT_DISCLAIMER = (
    "Этот ассистент предоставляет общую справочную информацию и не заменяет консультацию специалиста."
)
DEFAULT_TITLE = "Спутник"
DEFAULT_COLOR = "#fc7f03"
_HEX = set("0123456789abcdef")
MAX_WIDGET_TITLE = 120
MAX_WIDGET_DESCRIPTION = 150
MAX_DISCLAIMER = 250
MAX_LEGAL_NOTES = 3000
MAX_STARTER_QUESTION = 240
MAX_GREETING = 500
PARKINSON_GREETING = (
    "Здравствуйте! Я помогу найти информацию о болезни Паркинсона на основе проверенных материалов."
)


def normalize_web_color(value: str) -> str:
    """Store a CSS hex color as ``#rrggbb``."""
    text = value.strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(char in _HEX for char in text):
        text = "".join(char * 2 for char in text)
    if len(text) != 6 or any(char not in _HEX for char in text):
        raise PlatformError("Цвет укажите в виде #fc7f03")
    return f"#{text}"


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
    phone: str = ""


@dataclass(frozen=True)
class WidgetSettings:
    tenant_id: int
    title: str
    description: str
    primary_color: str
    show_sources: bool
    greeting: str
    disclaimer: str
    legal_notes: str
    logo_name: str


@dataclass(frozen=True)
class StarterQuestion:
    id: int
    text: str


@dataclass(frozen=True)
class IssuedReset:
    email: str
    slug: str
    tenant_id: int
    token: str


@dataclass(frozen=True)
class IssuedSignup:
    email: str
    token: str


@dataclass(frozen=True)
class SignupView:
    name: str
    phone: str
    email: str
    step: str
    slug: str
    expires_at: str


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
                "INSERT INTO users (tenant_id, email, password_hash, created_at, phone) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, email, hash_password(password), now, ""),
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
        if slug == "parkinson":
            self.seed_parkinson_skills(tenant.id)
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

    def start_signup(self, *, name: str, phone: str, email: str, ttl_seconds: int) -> IssuedSignup:
        """Store an application and return a reusable link token. No organization yet."""
        name = name.strip()
        email = email.strip().lower()
        try:
            phone = normalize_phone(phone)
        except ValueError as exc:
            raise PlatformError(str(exc)) from exc
        if not name or len(name) > 200:
            raise PlatformError("Укажите название организации")
        if not valid_email(email):
            raise PlatformError("Укажите корректный email")
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        stamp = now.isoformat()
        with self._lock, self._session() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise EmailTakenError("Этот email уже зарегистрирован")
            existing = conn.execute(
                "SELECT id, expires_at, submitted_at FROM signups WHERE email = ?",
                (email,),
            ).fetchone()
            if existing is not None:
                if existing["submitted_at"] or not _expired(existing["expires_at"], now):
                    raise EmailTakenError("Этот email уже зарегистрирован")
                conn.execute("DELETE FROM signups WHERE id = ?", (existing["id"],))
            conn.execute(
                """
                INSERT INTO signups (name, phone, email, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, phone, email, _token_hash(token), expires, stamp),
            )
        return IssuedSignup(email=email, token=token)

    def cancel_signup(self, token: str) -> None:
        """Drop an application that never became an organization, so the mailbox can be reused."""
        with self._lock, self._session() as conn:
            conn.execute(
                "DELETE FROM signups WHERE token_hash = ? AND submitted_at IS NULL",
                (_token_hash(token.strip()),),
            )

    def open_signup(self, token: str) -> SignupView:
        with self._lock, self._session() as conn:
            row = self._signup_row(conn, token)
            if row["email_verified_at"] is None and row["submitted_at"] is None:
                conn.execute(
                    "UPDATE signups SET email_verified_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
        return self._signup_view(token)

    def save_signup_organization(
        self, token: str, *, slug: str, password: str, phone: str | None = None
    ) -> SignupView:
        slug = slug.strip().lower()
        if len(slug) < 2 or not slug_available(slug):
            raise InvalidSlugError("Такой адрес поддомена использовать нельзя")
        if len(password) < 8 or len(password) > 256:
            raise PlatformError("Пароль должен быть не короче 8 символов")
        if phone is not None:
            try:
                phone = normalize_phone(phone)
            except ValueError as exc:
                raise PlatformError(str(exc)) from exc
        now = datetime.now(UTC)
        with self._lock, self._session() as conn:
            row = self._signup_row(conn, token, now=now, allow_finished=False)
            if conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
                raise SlugTakenError("Этот поддомен уже занят")
            conn.execute(
                """
                UPDATE signups SET slug = NULL
                WHERE slug = ? AND id != ? AND submitted_at IS NULL AND expires_at <= ?
                """,
                (slug, row["id"], now.isoformat()),
            )
            taken = conn.execute(
                "SELECT 1 FROM signups WHERE slug = ? AND id != ? AND submitted_at IS NULL",
                (slug, row["id"]),
            ).fetchone()
            if taken:
                raise SlugTakenError("Этот поддомен уже занят")
            if phone is None:
                phone = row["phone"]
            conn.execute(
                """
                UPDATE signups
                SET slug = ?, password_hash = ?, phone = ?, email_verified_at = COALESCE(email_verified_at, ?)
                WHERE id = ?
                """,
                (slug, hash_password(password), phone, now.isoformat(), row["id"]),
            )
        return self._signup_view(token)

    def complete_signup(
        self,
        token: str,
        *,
        widget_title: str,
        widget_description: str,
        primary_color: str,
        prompt_path: str,
    ) -> Tenant:
        title = widget_title.strip()
        description = widget_description.strip()
        if not title or len(title) > MAX_WIDGET_TITLE:
            raise PlatformError("Укажите название в шапке виджета, не длиннее 120 символов")
        if len(description) > MAX_WIDGET_DESCRIPTION:
            raise PlatformError("Описание виджета длиннее 150 символов")
        color = normalize_web_color(primary_color)
        now = datetime.now(UTC)
        stamp = now.isoformat()
        with self._lock, self._session() as conn:
            row = self._signup_row(conn, token, now=now, allow_finished=False)
            slug = (row["slug"] or "").strip()
            if not slug or not row["password_hash"]:
                raise PlatformError("Сначала укажите адрес и пароль")
            if conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
                raise SlugTakenError("Этот поддомен уже занят")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (row["email"],)).fetchone():
                raise EmailTakenError("Этот email уже зарегистрирован")
            cursor = conn.execute(
                """
                INSERT INTO tenants (slug, name, status, collection_name, prompt_path, created_at)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (slug, row["name"], f"tenant_{slug}", prompt_path, stamp),
            )
            tenant_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO users (tenant_id, email, password_hash, created_at, phone)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tenant_id, row["email"], row["password_hash"], stamp, row["phone"]),
            )
            self._insert_default_settings(
                conn,
                tenant_id,
                title=title,
                description=description,
                primary_color=color,
            )
            conn.execute(
                """
                UPDATE signups
                SET widget_title = ?, widget_description = ?, primary_color = ?, submitted_at = ?
                WHERE id = ?
                """,
                (title, description, color, stamp, row["id"]),
            )
        tenant = self.get(tenant_id)
        if tenant is None:
            raise PlatformError("Не удалось создать заявку")
        return tenant

    def _signup_row(
        self,
        conn: sqlite3.Connection,
        token: str,
        *,
        now: datetime | None = None,
        allow_finished: bool = True,
    ) -> sqlite3.Row:
        token = token.strip()
        if not token:
            raise PlatformError("Ссылка недействительна или устарела")
        row = conn.execute("SELECT * FROM signups WHERE token_hash = ?", (_token_hash(token),)).fetchone()
        if row is None:
            raise PlatformError("Ссылка недействительна или устарела")
        current = now or datetime.now(UTC)
        if row["submitted_at"]:
            if allow_finished:
                return row
            raise PlatformError("Заявка уже отправлена")
        if _expired(row["expires_at"], current):
            raise PlatformError("Ссылка недействительна или устарела")
        return row

    def _signup_view(self, token: str) -> SignupView:
        with self._lock, self._session() as conn:
            row = self._signup_row(conn, token)
        if row["submitted_at"]:
            step = "done"
        elif row["slug"]:
            step = "widget"
        else:
            step = "organization"
        try:
            phone = format_phone(row["phone"])
        except ValueError:
            phone = row["phone"]
        return SignupView(
            name=row["name"],
            phone=phone,
            email=row["email"],
            step=step,
            slug=row["slug"] or "",
            expires_at=row["expires_at"],
        )

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
                "INSERT INTO users (tenant_id, email, password_hash, created_at, phone) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, owner_email, hash_password(password), now, ""),
            )
            self._insert_default_settings(conn, tenant_id)
        tenant = self.get_by_slug(slug)
        if tenant is None:
            raise PlatformError("Не удалось создать организацию")
        if slug == "parkinson":
            self.seed_parkinson_skills(tenant.id)
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

    def list_starter_questions(self, tenant_id: int) -> list[StarterQuestion]:
        with self._lock, self._session() as conn:
            rows = conn.execute(
                "SELECT id, text FROM starter_questions WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()
        return [StarterQuestion(id=int(row["id"]), text=row["text"]) for row in rows]

    def sample_starter_questions(
        self, tenant_id: int, count: int, rng: random.Random | None = None
    ) -> list[str]:
        pool = [item.text for item in self.list_starter_questions(tenant_id)]
        if count <= 0 or not pool:
            return []
        return (rng or random).sample(pool, min(count, len(pool)))

    def add_starter_question(self, tenant_id: int, text: str) -> StarterQuestion:
        text = _starter_text(text)
        with self._lock, self._session() as conn:
            if self.get(tenant_id) is None:
                raise TenantNotFoundError("Организация не найдена")
            if conn.execute(
                "SELECT 1 FROM starter_questions WHERE tenant_id = ? AND text = ?",
                (tenant_id, text),
            ).fetchone():
                raise PlatformError("Такой вопрос уже есть")
            cursor = conn.execute(
                "INSERT INTO starter_questions (tenant_id, text, created_at) VALUES (?, ?, ?)",
                (tenant_id, text, _now()),
            )
            question_id = int(cursor.lastrowid)
        return StarterQuestion(id=question_id, text=text)

    def update_starter_question(self, tenant_id: int, question_id: int, text: str) -> StarterQuestion:
        text = _starter_text(text)
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT id FROM starter_questions WHERE id = ? AND tenant_id = ?",
                (question_id, tenant_id),
            ).fetchone()
            if row is None:
                raise TenantNotFoundError("Вопрос не найден")
            taken = conn.execute(
                "SELECT 1 FROM starter_questions WHERE tenant_id = ? AND text = ? AND id != ?",
                (tenant_id, text, question_id),
            ).fetchone()
            if taken:
                raise PlatformError("Такой вопрос уже есть")
            conn.execute(
                "UPDATE starter_questions SET text = ? WHERE id = ? AND tenant_id = ?",
                (text, question_id, tenant_id),
            )
        return StarterQuestion(id=question_id, text=text)

    def delete_starter_question(self, tenant_id: int, question_id: int) -> None:
        with self._lock, self._session() as conn:
            deleted = conn.execute(
                "DELETE FROM starter_questions WHERE id = ? AND tenant_id = ?",
                (question_id, tenant_id),
            ).rowcount
        if not deleted:
            raise TenantNotFoundError("Вопрос не найден")

    # --- skills -------------------------------------------------------------------------

    def skills_revision(self, tenant_id: int) -> int:
        """Monotonic-ish cache key: changes when any skill or example row changes."""
        with self._lock, self._session() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(s.id), 0) + COALESCE(MAX(s.updated_at), '')
                       || COUNT(s.id) || ':' || COUNT(e.id)
                AS rev
                FROM skills s
                LEFT JOIN skill_examples e ON e.skill_id = s.id
                WHERE s.tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
        return hash(str(row["rev"] if row else "")) & 0x7FFFFFFF

    def list_skills(self, tenant_id: int, *, enabled_only: bool = False) -> list[Skill]:
        with self._lock, self._session() as conn:
            sql = "SELECT * FROM skills WHERE tenant_id = ?"
            if enabled_only:
                sql += " AND enabled = 1"
            sql += " ORDER BY priority DESC, name"
            rows = conn.execute(sql, (tenant_id,)).fetchall()
            return [self._skill_from_row(conn, row) for row in rows]

    def get_skill(self, tenant_id: int, skill_id: int) -> Skill:
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE id = ? AND tenant_id = ?",
                (skill_id, tenant_id),
            ).fetchone()
            if row is None:
                raise TenantNotFoundError("Навык не найден")
            return self._skill_from_row(conn, row)

    def create_skill(self, tenant_id: int, payload: dict[str, Any]) -> Skill:
        fields = _validate_skill_payload(payload, partial=False)
        with self._lock, self._session() as conn:
            if self.get(tenant_id) is None:
                raise TenantNotFoundError("Организация не найдена")
            if conn.execute(
                "SELECT 1 FROM skills WHERE tenant_id = ? AND name = ?",
                (tenant_id, fields["name"]),
            ).fetchone():
                raise PlatformError(f"Навык «{fields['name']}» уже есть")
            stamp = _now()
            cursor = conn.execute(
                """
                INSERT INTO skills (
                    tenant_id, name, title, skill_type, activation, description, body,
                    priority, sticky_turns, enabled, retrieval_boost, query_hints,
                    allowed_tools, config, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    fields["name"],
                    fields["title"],
                    fields["skill_type"],
                    fields["activation"],
                    fields["description"],
                    fields["body"],
                    fields["priority"],
                    fields["sticky_turns"],
                    1 if fields["enabled"] else 0,
                    json.dumps(fields["retrieval_boost"], ensure_ascii=False),
                    json.dumps(fields["query_hints"], ensure_ascii=False),
                    json.dumps(fields["allowed_tools"], ensure_ascii=False),
                    json.dumps(fields["config"], ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            skill_id = int(cursor.lastrowid)
            self._replace_examples(conn, skill_id, fields.get("examples") or [])
            return self._skill_from_row(
                conn,
                conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone(),
            )

    def update_skill(self, tenant_id: int, skill_id: int, payload: dict[str, Any]) -> Skill:
        fields = _validate_skill_payload(payload, partial=True)
        with self._lock, self._session() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE id = ? AND tenant_id = ?",
                (skill_id, tenant_id),
            ).fetchone()
            if row is None:
                raise TenantNotFoundError("Навык не найден")
            if "name" in fields and fields["name"] != row["name"]:
                taken = conn.execute(
                    "SELECT 1 FROM skills WHERE tenant_id = ? AND name = ? AND id != ?",
                    (tenant_id, fields["name"], skill_id),
                ).fetchone()
                if taken:
                    raise PlatformError(f"Навык «{fields['name']}» уже есть")
            columns = {
                "name": "name",
                "title": "title",
                "skill_type": "skill_type",
                "activation": "activation",
                "description": "description",
                "body": "body",
                "priority": "priority",
                "sticky_turns": "sticky_turns",
            }
            sets: list[str] = []
            values: list[Any] = []
            for key, column in columns.items():
                if key in fields:
                    sets.append(f"{column} = ?")
                    values.append(fields[key])
            if "enabled" in fields:
                sets.append("enabled = ?")
                values.append(1 if fields["enabled"] else 0)
            for key in ("retrieval_boost", "query_hints", "allowed_tools", "config"):
                if key in fields:
                    sets.append(f"{key} = ?")
                    values.append(json.dumps(fields[key], ensure_ascii=False))
            if sets:
                sets.append("updated_at = ?")
                values.append(_now())
                values.extend([skill_id, tenant_id])
                conn.execute(
                    f"UPDATE skills SET {', '.join(sets)} WHERE id = ? AND tenant_id = ?",
                    values,
                )
            if "examples" in fields:
                self._replace_examples(conn, skill_id, fields["examples"] or [])
                conn.execute(
                    "UPDATE skills SET updated_at = ? WHERE id = ? AND tenant_id = ?",
                    (_now(), skill_id, tenant_id),
                )
            return self._skill_from_row(
                conn,
                conn.execute(
                    "SELECT * FROM skills WHERE id = ? AND tenant_id = ?",
                    (skill_id, tenant_id),
                ).fetchone(),
            )

    def delete_skill(self, tenant_id: int, skill_id: int) -> None:
        with self._lock, self._session() as conn:
            conn.execute("DELETE FROM skill_examples WHERE skill_id = ?", (skill_id,))
            deleted = conn.execute(
                "DELETE FROM skills WHERE id = ? AND tenant_id = ?",
                (skill_id, tenant_id),
            ).rowcount
        if not deleted:
            raise TenantNotFoundError("Навык не найден")

    def seed_parkinson_skills(self, tenant_id: int) -> list[Skill]:
        """Idempotent seed of domain templates (skip names that already exist).

        For existing skills, fills empty ``config.suggestions`` from the template
        without overwriting custom suggestion lists.
        """
        if self.get(tenant_id) is None:
            raise TenantNotFoundError("Организация не найдена")
        by_name = {skill.name: skill for skill in self.list_skills(tenant_id)}
        created: list[Skill] = []
        for raw in PARKINSON_SKILL_SEEDS:
            current = by_name.get(raw["name"])
            if current is None:
                created.append(self.create_skill(tenant_id, raw))
                continue
            want = (raw.get("config") or {}).get("suggestions")
            if not want:
                continue
            cfg = dict(current.config or {})
            if cfg.get("suggestions"):
                continue
            cfg["suggestions"] = list(want)
            self.update_skill(tenant_id, current.id, {"config": cfg})
        return created

    def _skill_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Skill:
        examples = conn.execute(
            "SELECT id, text FROM skill_examples WHERE skill_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
        return Skill(
            id=int(row["id"]),
            tenant_id=int(row["tenant_id"]),
            name=row["name"],
            title=row["title"],
            skill_type=row["skill_type"],
            activation=row["activation"],
            description=row["description"],
            body=row["body"],
            priority=int(row["priority"]),
            sticky_turns=int(row["sticky_turns"]),
            enabled=bool(row["enabled"]),
            retrieval_boost=_json_list(row["retrieval_boost"]),
            query_hints=_json_list(row["query_hints"]),
            allowed_tools=_json_list(row["allowed_tools"]),
            config=_json_dict(row["config"]),
            examples=tuple(SkillExample(id=int(ex["id"]), text=ex["text"]) for ex in examples),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _replace_examples(conn: sqlite3.Connection, skill_id: int, examples: list[str]) -> None:
        conn.execute("DELETE FROM skill_examples WHERE skill_id = ?", (skill_id,))
        seen: set[str] = set()
        for raw in examples:
            text = str(raw).strip()
            if not text or text in seen:
                continue
            if len(text) > 500:
                raise PlatformError("Пример длиннее 500 символов")
            seen.add(text)
            conn.execute(
                "INSERT INTO skill_examples (skill_id, text) VALUES (?, ?)",
                (skill_id, text),
            )

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
            greeting=row["greeting"] or "",
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
        greeting: str,
        disclaimer: str,
        legal_notes: str,
        logo_name: str | None = None,
    ) -> WidgetSettings:
        title = title.strip()
        description = description.strip()
        greeting = greeting.strip()
        disclaimer = disclaimer.strip()
        legal_notes = legal_notes.strip()
        if not title:
            raise PlatformError("Укажите заголовок виджета")
        if len(title) > MAX_WIDGET_TITLE:
            raise PlatformError("Название виджета длиннее 120 символов")
        if len(description) > MAX_WIDGET_DESCRIPTION:
            raise PlatformError("Описание виджета длиннее 150 символов")
        if len(greeting) > MAX_GREETING:
            raise PlatformError(f"Приветствие длиннее {MAX_GREETING} символов")
        if not disclaimer:
            raise PlatformError("Укажите юридическую информацию")
        if len(disclaimer) > MAX_DISCLAIMER:
            raise PlatformError("Юридическая информация длиннее 250 символов")
        if len(legal_notes) > MAX_LEGAL_NOTES:
            raise PlatformError("Подробная юридическая информация длиннее 3000 символов")
        primary_color = normalize_web_color(primary_color)
        with self._lock, self._session() as conn:
            fields = [
                title,
                description,
                primary_color,
                int(show_sources),
                greeting,
                disclaimer,
                legal_notes,
            ]
            sql = """
                UPDATE widget_settings
                SET title = ?, description = ?, primary_color = ?, show_sources = ?,
                    greeting = ?, disclaimer = ?, legal_notes = ?
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
        primary_color: str = DEFAULT_COLOR,
    ) -> None:
        conn.execute(
            """
            INSERT INTO widget_settings (
                tenant_id, title, description, primary_color, show_sources,
                greeting, disclaimer, legal_notes, logo_name
            ) VALUES (?, ?, ?, ?, 1, '', ?, '', '')
            """,
            (tenant_id, title, description, primary_color, DEFAULT_DISCLAIMER),
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
                    greeting TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS starter_questions (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, text)
                );
                CREATE TABLE IF NOT EXISTS signups (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    email_verified_at TEXT,
                    slug TEXT,
                    password_hash TEXT,
                    widget_title TEXT,
                    widget_description TEXT,
                    primary_color TEXT,
                    submitted_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    skill_type TEXT NOT NULL DEFAULT 'domain',
                    activation TEXT NOT NULL DEFAULT 'match',
                    description TEXT NOT NULL,
                    body TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    sticky_turns INTEGER NOT NULL DEFAULT 2,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    retrieval_boost TEXT NOT NULL DEFAULT '[]',
                    query_hints TEXT NOT NULL DEFAULT '[]',
                    allowed_tools TEXT NOT NULL DEFAULT '[]',
                    config TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (tenant_id, name)
                );
                CREATE INDEX IF NOT EXISTS idx_skills_tenant_enabled_type
                    ON skills (tenant_id, enabled, skill_type);
                CREATE TABLE IF NOT EXISTS skill_examples (
                    id INTEGER PRIMARY KEY,
                    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    UNIQUE (skill_id, text)
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "phone" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
            widget_columns = {row[1] for row in conn.execute("PRAGMA table_info(widget_settings)")}
            if "greeting" not in widget_columns:
                conn.execute("ALTER TABLE widget_settings ADD COLUMN greeting TEXT NOT NULL DEFAULT ''")
            self._backfill_emails(conn)
            self._seed_parkinson_questions(conn)
            self._seed_parkinson_greeting(conn)
            self._seed_parkinson_skills_on_init(conn)

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

    def _seed_parkinson_questions(self, conn: sqlite3.Connection) -> None:
        """Copy the fund catalog into the parkinson account once, so it stays isolated."""
        row = conn.execute("SELECT id FROM tenants WHERE slug = 'parkinson'").fetchone()
        if row is None:
            return
        tenant_id = int(row["id"])
        if conn.execute(
            "SELECT 1 FROM starter_questions WHERE tenant_id = ?", (tenant_id,)
        ).fetchone():
            return
        catalog = Path(__file__).resolve().parents[2] / "config" / "starter_questions.json"
        if not catalog.is_file():
            return
        try:
            payload = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw = payload.get("questions") if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            return
        stamp = _now()
        seen: set[str] = set()
        for item in raw:
            try:
                text = _starter_text(str(item))
            except PlatformError:
                continue
            if text in seen:
                continue
            seen.add(text)
            conn.execute(
                "INSERT INTO starter_questions (tenant_id, text, created_at) VALUES (?, ?, ?)",
                (tenant_id, text, stamp),
            )

    def _seed_parkinson_greeting(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE widget_settings
            SET greeting = ?
            WHERE greeting = ''
              AND tenant_id = (SELECT id FROM tenants WHERE slug = 'parkinson')
            """,
            (PARKINSON_GREETING,),
        )

    def _seed_parkinson_skills_on_init(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT id FROM tenants WHERE slug = 'parkinson'").fetchone()
        if row is None:
            return
        tenant_id = int(row["id"])
        # Release the init connection before create_skill (it opens its own).
        # Call after _init's with-block via deferred flag is awkward; insert inline instead.
        existing = {
            r["name"]
            for r in conn.execute("SELECT name FROM skills WHERE tenant_id = ?", (tenant_id,)).fetchall()
        }
        stamp = _now()
        for raw in PARKINSON_SKILL_SEEDS:
            if raw["name"] in existing:
                continue
            fields = _validate_skill_payload(raw, partial=False)
            cursor = conn.execute(
                """
                INSERT INTO skills (
                    tenant_id, name, title, skill_type, activation, description, body,
                    priority, sticky_turns, enabled, retrieval_boost, query_hints,
                    allowed_tools, config, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    fields["name"],
                    fields["title"],
                    fields["skill_type"],
                    fields["activation"],
                    fields["description"],
                    fields["body"],
                    fields["priority"],
                    fields["sticky_turns"],
                    1,
                    json.dumps(fields["retrieval_boost"], ensure_ascii=False),
                    json.dumps(fields["query_hints"], ensure_ascii=False),
                    json.dumps(fields["allowed_tools"], ensure_ascii=False),
                    json.dumps(fields["config"], ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
            self._replace_examples(conn, int(cursor.lastrowid), fields.get("examples") or [])


_TENANT_SQL = """
    SELECT t.id, t.slug, t.name, t.status, t.collection_name, t.prompt_path, t.created_at,
           u.email, u.phone
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
        phone=row["phone"] or "",
    )


def _expired(value: str, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(value)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires < now


def _starter_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise PlatformError("Укажите текст вопроса")
    if len(text) > MAX_STARTER_QUESTION:
        raise PlatformError(f"Вопрос длиннее {MAX_STARTER_QUESTION} символов")
    return text


def _json_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _json_dict(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _validate_skill_payload(payload: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    required = ("name", "title", "description", "body")
    if not partial:
        for key in required:
            if key not in payload or payload[key] is None:
                raise PlatformError(f"Поле «{key}» обязательно")

    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip().lower()
        if not re.fullmatch(NAME_PATTERN, name) or len(name) > 64:
            raise PlatformError("name: латиница, цифры и дефис, до 64 символов")
        fields["name"] = name

    if "title" in payload and payload["title"] is not None:
        title = str(payload["title"]).strip()
        if not title or len(title) > 120:
            raise PlatformError("title обязателен, до 120 символов")
        fields["title"] = title

    if "description" in payload and payload["description"] is not None:
        description = str(payload["description"]).strip()
        if not description or len(description) > 1024:
            raise PlatformError("description обязателен, до 1024 символов")
        fields["description"] = description

    if "body" in payload and payload["body"] is not None:
        body = str(payload["body"]).strip()
        if not body or len(body) > 20000:
            raise PlatformError("body обязателен, до 20000 символов")
        fields["body"] = body

    if "skill_type" in payload or "type" in payload:
        skill_type = str(payload.get("skill_type") or payload.get("type") or "domain").strip()
        if skill_type not in SKILL_TYPES:
            raise PlatformError("type: domain, context или workflow")
        fields["skill_type"] = skill_type
    elif not partial:
        fields["skill_type"] = "domain"

    if "activation" in payload and payload["activation"] is not None:
        activation = str(payload["activation"]).strip()
        if activation not in ACTIVATIONS:
            raise PlatformError("activation: match, always, intent или manual")
        fields["activation"] = activation
    elif not partial:
        fields["activation"] = "match"

    if "priority" in payload and payload["priority"] is not None:
        priority = int(payload["priority"])
        if not 0 <= priority <= 100:
            raise PlatformError("priority: 0–100")
        fields["priority"] = priority
    elif not partial:
        fields["priority"] = 50

    if "sticky_turns" in payload and payload["sticky_turns"] is not None:
        sticky = int(payload["sticky_turns"])
        if not 0 <= sticky <= 20:
            raise PlatformError("sticky_turns: 0–20")
        fields["sticky_turns"] = sticky
    elif not partial:
        fields["sticky_turns"] = 2

    if "enabled" in payload and payload["enabled"] is not None:
        fields["enabled"] = bool(payload["enabled"])
    elif not partial:
        fields["enabled"] = True

    for key in ("retrieval_boost", "query_hints", "allowed_tools"):
        if key in payload and payload[key] is not None:
            raw = payload[key]
            if not isinstance(raw, list):
                raise PlatformError(f"{key} должен быть списком строк")
            fields[key] = [str(item).strip() for item in raw if str(item).strip()]
        elif not partial:
            fields[key] = []

    if "config" in payload and payload["config"] is not None:
        if not isinstance(payload["config"], dict):
            raise PlatformError("config должен быть объектом")
        fields["config"] = payload["config"]
    elif not partial:
        fields["config"] = {}

    if "examples" in payload and payload["examples"] is not None:
        if not isinstance(payload["examples"], list):
            raise PlatformError("examples должен быть списком строк")
        fields["examples"] = [str(item).strip() for item in payload["examples"] if str(item).strip()]

    return fields


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
