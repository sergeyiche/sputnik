"""Admin API: knowledge base files, conversion and full import."""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from apps.api.app.config import settings
from apps.api.app.deps import (
    get_admin_auth,
    get_ingest_runner,
    get_knowledge_files_service,
    get_vector_store,
)
from packages.admin.ingest_jobs import IngestAlreadyRunningError, IngestJobRunner
from packages.admin.knowledge_files import (
    FileExistsConflictError,
    FileMissingError,
    FileTooLargeError,
    InvalidFileError,
    KnowledgeFilesService,
)
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
    if not auth.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Админка не настроена: задайте ADMIN_USERNAME и ADMIN_PASSWORD")
    if not auth.validate(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: AdminAuthService = Depends(get_admin_auth),
    token: str | None = Cookie(default=None, alias=settings.admin_cookie_name),
) -> Response:
    auth.logout(token)
    response.delete_cookie(settings.admin_cookie_name, path=COOKIE_PATH)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse, dependencies=[Depends(require_admin)])
def me() -> MeResponse:
    return MeResponse(username=settings.admin_username)


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
