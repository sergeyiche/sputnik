"""Admin API: knowledge base files, conversion, full import and the system prompt."""

from __future__ import annotations

import ipaddress
import logging
import time
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import (
    get_admin_auth,
    get_ingest_runner,
    get_knowledge_files_service,
    get_mailer,
    get_platform_store,
    get_system_prompt_store,
    get_vector_store,
)
from apps.api.app.tenant_sessions import drop as drop_tenant_session
from apps.api.app.tenant_sessions import drop_for_tenant
from apps.api.app.tenant_sessions import issue as issue_tenant_session
from apps.api.app.tenant_sessions import tenant_id_for
from packages.integrations.mailtrap import MailtrapError, MailtrapMailer
from packages.platform.context import scope_var, tenant_var
from packages.platform.emails import valid_email
from packages.platform.store import MAX_STARTER_QUESTION, PlatformError, PlatformStore, Tenant, TenantNotFoundError
from packages.admin.ingest_jobs import IngestAlreadyRunningError, IngestJobRunner
from packages.admin.knowledge_files import (
    FileExistsConflictError,
    FileMissingError,
    FileTooLargeError,
    InvalidFileError,
    KnowledgeFilesService,
)
from packages.admin.prompt_store import (
    PromptSnapshot,
    PromptUnchangedError,
    PromptValidationError,
    PromptVersionNotFoundError,
    SystemPromptStore,
)
from packages.rag.prompts import reload_system_prompt
from packages.etl.convert import ConvertResult
from packages.etl.converters import SUPPORTED_EXTENSIONS
from packages.knowledge.base import VectorStore
from packages.security.admin_auth import (
    AdminAuthNotConfiguredError,
    AdminAuthService,
    TooManyAttemptsError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

COOKIE_PATH = "/v1/admin"
_RESET_WINDOW_SECONDS = 15 * 60
_RESET_LIMIT = 5
_reset_attempts: dict[str, list[float]] = {}


def _forgot_detail() -> str:
    minutes = settings.password_reset_ttl_minutes
    return (
        "Если эта почта привязана к кабинету, мы отправили письмо со ссылкой. "
        f"Она действует {minutes} мин."
    )


# --- auth ------------------------------------------------------------------------------


def _client_id(request: Request) -> str:
    """Client IP for login throttling; trusts X-Real-IP only from private proxies (nginx)."""
    host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-real-ip", "").strip()
    try:
        if forwarded and ipaddress.ip_address(host).is_private:
            return forwarded
    except ValueError:
        pass
    return host


def require_admin(
    auth: AdminAuthService = Depends(get_admin_auth),
    token: str | None = Cookie(default=None, alias=settings.admin_cookie_name),
) -> None:
    scope = scope_var.get()
    if scope.kind == "tenant":
        tenant = tenant_var.get()
        if tenant is None or tenant.status not in {"pending", "active", "suspended"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена или отклонена")
        if tenant_id_for(token) != tenant.id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
        return
    if not auth.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Админка не настроена: задайте ADMIN_USERNAME и ADMIN_PASSWORD")
    if not auth.validate(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=256)


class MeResponse(BaseModel):
    username: str


@router.post("/login", response_model=MeResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: AdminAuthService = Depends(get_admin_auth),
) -> MeResponse:
    if scope_var.get().kind == "tenant":
        return _login_tenant(payload, response)

    client = _client_id(request)
    try:
        token = auth.login(payload.username, payload.password, client)
    except AdminAuthNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except TooManyAttemptsError as exc:
        logger.warning("Admin login throttled for %s", client)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc

    if token is None:
        logger.warning("Admin login failed for %s (user=%r)", client, payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=auth.session_ttl_seconds,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )
    logger.info("Admin logged in from %s", client)
    return MeResponse(username=payload.username)


def _login_tenant(payload: LoginRequest, response: Response) -> MeResponse:
    tenant = tenant_var.get()
    if tenant is None or tenant.status not in {"pending", "active", "suspended"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена или отклонена")
    owner = get_platform_store().authenticate(payload.username, payload.password)
    if owner is None or owner.id != tenant.id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    token = issue_tenant_session(tenant.id, settings.admin_session_ttl_minutes * 60)
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=settings.admin_session_ttl_minutes * 60,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )
    return MeResponse(username=payload.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: AdminAuthService = Depends(get_admin_auth),
    token: str | None = Cookie(default=None, alias=settings.admin_cookie_name),
) -> Response:
    auth.logout(token)
    drop_tenant_session(token)
    response.delete_cookie(settings.admin_cookie_name, path=COOKIE_PATH)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse, dependencies=[Depends(require_admin)])
def me(token: str | None = Cookie(default=None, alias=settings.admin_cookie_name)) -> MeResponse:
    tenant = tenant_var.get()
    if scope_var.get().kind == "tenant" and tenant is not None:
        return MeResponse(username=tenant.email)
    return MeResponse(username=settings.admin_username)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=512)
    password: str = Field(..., min_length=8, max_length=256)


class MessageResponse(BaseModel):
    detail: str


def clear_password_reset_throttle() -> None:
    _reset_attempts.clear()


def _throttle_password_reset(client: str, email: str) -> None:
    key = f"{client}|{email.strip().lower()}"
    now = time.monotonic()
    hits = [stamp for stamp in _reset_attempts.get(key, []) if now - stamp < _RESET_WINDOW_SECONDS]
    if len(hits) >= _RESET_LIMIT:
        _reset_attempts[key] = hits
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много запросов. Попробуйте позже.",
            headers={"Retry-After": "900"},
        )
    hits.append(now)
    _reset_attempts[key] = hits


def _owner_tenant() -> Tenant:
    tenant = tenant_var.get()
    if scope_var.get().kind != "tenant" or tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Восстановление пароля доступно в кабинете организации")
    return tenant


def _reset_link(request: Request, slug: str, token: str) -> str:
    base = settings.platform_base_domain.strip().lower().strip(".")
    raw = request.headers.get("host", "").split(",")[0].strip().lower()
    hostname, separator, port = raw.partition(":")
    allowed = {f"{slug}.{base}"}
    if hostname.endswith(".localhost"):
        allowed.add(hostname)
    if hostname not in allowed:
        hostname = f"{slug}.{base}"
        separator = ""
        port = ""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    scheme = forwarded if forwarded in {"http", "https"} else request.url.scheme
    if scheme not in {"http", "https"}:
        scheme = "https"
    host = f"{hostname}:{port}" if separator and port else hostname
    return f"{scheme}://{host}/admin/#reset={quote(token, safe='')}"


@router.post("/password/forgot", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    store: PlatformStore = Depends(get_platform_store),
    mailer: MailtrapMailer = Depends(get_mailer),
) -> MessageResponse:
    tenant = _owner_tenant()
    email = payload.email.strip().lower()
    if not valid_email(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите корректный email")
    _throttle_password_reset(_client_id(request), email)
    if not mailer.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Отправка писем не настроена")
    issued = store.issue_password_reset(
        email,
        tenant_id=tenant.id,
        ttl_seconds=settings.password_reset_ttl_minutes * 60,
    )
    if issued is None:
        return MessageResponse(detail=_forgot_detail())
    minutes = settings.password_reset_ttl_minutes
    link = _reset_link(request, tenant.slug, issued.token)
    text = (
        "Здравствуйте.\n\n"
        "Чтобы задать новый пароль кабинета Спутник, откройте ссылку "
        f"(она действует {minutes} мин.):\n\n"
        f"{link}\n\n"
        "Если вы не запрашивали восстановление, ничего делать не нужно.\n"
    )
    try:
        mailer.send(to_email=issued.email, subject="Восстановление пароля кабинета Спутник", text=text)
    except MailtrapError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return MessageResponse(detail=_forgot_detail())


@router.post("/password/reset", response_model=MessageResponse)
def reset_password(
    payload: ResetPasswordRequest,
    store: PlatformStore = Depends(get_platform_store),
) -> MessageResponse:
    try:
        tenant = store.reset_password(payload.token, payload.password)
    except PlatformError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    drop_for_tenant(tenant.id)
    return MessageResponse(detail="Пароль обновлён. Войдите с новым паролем.")


# --- sources (knowledge_sources) -------------------------------------------------------


class SourceItem(BaseModel):
    name: str
    title: str
    format: str
    size_bytes: int
    modified_at: datetime
    supported: bool
    conversion_status: str
    document: str | None


class SourcesResponse(BaseModel):
    items: list[SourceItem]
    supported_formats: list[str]
    max_upload_mb: int


class ConvertRequest(BaseModel):
    name: str | None = Field(default=None, description="Файл в knowledge_sources; пусто — все новые и изменённые")


class ConvertResultItem(BaseModel):
    source: str
    document: str
    status: str
    message: str = ""


class ConvertResponse(BaseModel):
    results: list[ConvertResultItem]


class DeleteResponse(BaseModel):
    deleted: list[str]


def _convert_item(result: ConvertResult) -> ConvertResultItem:
    return ConvertResultItem(
        source=result.source.name,
        document=result.output.name,
        status=result.status,
        message=result.message,
    )


def _file_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileMissingError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, FileExistsConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, FileTooLargeError):
        return HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/sources", response_model=SourcesResponse, dependencies=[Depends(require_admin)])
def list_sources(service: KnowledgeFilesService = Depends(get_knowledge_files_service)) -> SourcesResponse:
    return SourcesResponse(
        items=[SourceItem(**vars(item)) for item in service.list_sources()],
        supported_formats=sorted(ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS),
        max_upload_mb=service.max_upload_bytes // (1024 * 1024),
    )


@router.post(
    "/sources",
    response_model=SourceItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def upload_source(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    service: KnowledgeFilesService = Depends(get_knowledge_files_service),
) -> SourceItem:
    try:
        item = service.save_upload(file.filename or "", file.file, overwrite=overwrite)
    except (InvalidFileError, FileExistsConflictError, FileTooLargeError) as exc:
        raise _file_error(exc) from exc
    finally:
        file.file.close()
    return SourceItem(**vars(item))


@router.delete("/sources", response_model=DeleteResponse, dependencies=[Depends(require_admin)])
def delete_source(
    name: str = Query(..., min_length=1),
    service: KnowledgeFilesService = Depends(get_knowledge_files_service),
) -> DeleteResponse:
    try:
        return DeleteResponse(deleted=service.delete_source(name))
    except (InvalidFileError, FileMissingError) as exc:
        raise _file_error(exc) from exc


@router.post("/convert", response_model=ConvertResponse, dependencies=[Depends(require_admin)])
def convert(
    payload: ConvertRequest,
    service: KnowledgeFilesService = Depends(get_knowledge_files_service),
) -> ConvertResponse:
    try:
        results = [service.convert_source(payload.name)] if payload.name else service.convert_pending()
    except (InvalidFileError, FileMissingError) as exc:
        raise _file_error(exc) from exc
    return ConvertResponse(results=[_convert_item(r) for r in results])


# --- documents (knowledge) and import --------------------------------------------------


class DocumentItem(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime
    converted_at: datetime | None
    source_file: str | None
    in_index: bool


class DocumentsResponse(BaseModel):
    items: list[DocumentItem]
    chunk_count: int


class IngestStateResponse(BaseModel):
    status: str
    phase: str | None
    started_at: datetime | None
    finished_at: datetime | None
    documents: int
    chunks_total: int
    chunks_done: int
    duration_seconds: float | None
    error: str | None


@router.get("/documents", response_model=DocumentsResponse, dependencies=[Depends(require_admin)])
def list_documents(
    service: KnowledgeFilesService = Depends(get_knowledge_files_service),
    store: VectorStore = Depends(get_vector_store),
) -> DocumentsResponse:
    try:
        indexed = set(store.list_documents())
        chunk_count = store.count()
    except Exception:
        logger.exception("Cannot read vector store index")
        indexed, chunk_count = set(), 0
    return DocumentsResponse(
        items=[DocumentItem(**vars(item)) for item in service.list_documents(indexed)],
        chunk_count=chunk_count,
    )


@router.get("/ingest", response_model=IngestStateResponse, dependencies=[Depends(require_admin)])
def ingest_status(runner: IngestJobRunner = Depends(get_ingest_runner)) -> IngestStateResponse:
    return IngestStateResponse(**runner.state.to_dict())


@router.post(
    "/ingest",
    response_model=IngestStateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
def start_ingest(runner: IngestJobRunner = Depends(get_ingest_runner)) -> IngestStateResponse:
    try:
        state = runner.start()
    except IngestAlreadyRunningError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    logger.info("Admin started full import")
    return IngestStateResponse(**state.to_dict())


# --- system prompt ---------------------------------------------------------------------


class PromptVersionItem(BaseModel):
    id: str
    created_at: datetime
    note: str
    content: str
    active: bool


class PromptStateResponse(BaseModel):
    content: str
    mode: str
    active_version_id: str | None
    default_content: str
    versions: list[PromptVersionItem]


class SavePromptRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20_000)
    note: str = Field(default="", max_length=200)


class ActivatePromptRequest(BaseModel):
    version_id: str = Field(..., min_length=1, max_length=64)


def _prompt_response(snapshot: PromptSnapshot) -> PromptStateResponse:
    return PromptStateResponse(
        content=snapshot.content,
        mode=snapshot.mode,
        active_version_id=snapshot.active_version_id,
        default_content=snapshot.default_content,
        versions=[
            PromptVersionItem(
                id=item.id,
                created_at=item.created_at,
                note=item.note,
                content=item.content,
                active=item.id == snapshot.active_version_id,
            )
            for item in snapshot.versions
        ],
    )


def _apply_prompt(store: SystemPromptStore, snapshot: PromptSnapshot) -> PromptStateResponse:
    """Publish the new active text to the process that answers chat requests."""
    store.invalidate_cache()
    reload_system_prompt()
    return _prompt_response(snapshot)


@router.get("/prompt", response_model=PromptStateResponse, dependencies=[Depends(require_admin)])
def get_prompt(store: SystemPromptStore = Depends(get_system_prompt_store)) -> PromptStateResponse:
    return _prompt_response(store.snapshot())


@router.put("/prompt", response_model=PromptStateResponse, dependencies=[Depends(require_admin)])
def save_prompt(
    payload: SavePromptRequest,
    store: SystemPromptStore = Depends(get_system_prompt_store),
) -> PromptStateResponse:
    try:
        snapshot = store.save(payload.content, payload.note)
    except PromptUnchangedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PromptValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _apply_prompt(store, snapshot)


@router.post("/prompt/activate", response_model=PromptStateResponse, dependencies=[Depends(require_admin)])
def activate_prompt(
    payload: ActivatePromptRequest,
    store: SystemPromptStore = Depends(get_system_prompt_store),
) -> PromptStateResponse:
    try:
        snapshot = store.activate(payload.version_id)
    except PromptVersionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _apply_prompt(store, snapshot)


@router.post("/prompt/restore-default", response_model=PromptStateResponse, dependencies=[Depends(require_admin)])
def restore_default_prompt(store: SystemPromptStore = Depends(get_system_prompt_store)) -> PromptStateResponse:
    return _apply_prompt(store, store.restore_default())


class StarterQuestionBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_STARTER_QUESTION)


def _question_tenant() -> Tenant:
    tenant = tenant_var.get()
    if tenant is None or tenant.status not in {"pending", "active", "suspended"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена или отклонена")
    return tenant


def _question_payload(item) -> dict:
    return {"id": item.id, "text": item.text}


@router.get("/questions", dependencies=[Depends(require_admin)])
def list_questions(store: PlatformStore = Depends(get_platform_store)) -> dict:
    items = store.list_starter_questions(_question_tenant().id)
    return {"items": [_question_payload(item) for item in items]}


@router.post("/questions", dependencies=[Depends(require_admin)])
def add_question(payload: StarterQuestionBody, store: PlatformStore = Depends(get_platform_store)) -> dict:
    try:
        item = store.add_starter_question(_question_tenant().id, payload.text)
    except (PlatformError, TenantNotFoundError) as exc:
        code = status.HTTP_404_NOT_FOUND if isinstance(exc, TenantNotFoundError) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    return _question_payload(item)


@router.put("/questions/{question_id}", dependencies=[Depends(require_admin)])
def update_question(
    question_id: int,
    payload: StarterQuestionBody,
    store: PlatformStore = Depends(get_platform_store),
) -> dict:
    try:
        item = store.update_starter_question(_question_tenant().id, question_id, payload.text)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _question_payload(item)


@router.delete("/questions/{question_id}", dependencies=[Depends(require_admin)])
def delete_question(question_id: int, store: PlatformStore = Depends(get_platform_store)) -> Response:
    try:
        store.delete_starter_question(_question_tenant().id, question_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
