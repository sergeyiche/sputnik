"""Manage raw source files (knowledge_sources) and converted documents (knowledge)."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from packages.etl.convert import ConvertResult, convert_file, is_service_file
from packages.etl.converters import SUPPORTED_EXTENSIONS
from packages.knowledge.sources import INDEXED_SUFFIXES, read_header_fields

logger = logging.getLogger(__name__)

UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_FILENAME_LENGTH = 200


class KnowledgeFileError(Exception):
    """Base error with a user-facing message."""


class InvalidFileError(KnowledgeFileError):
    pass


class FileTooLargeError(KnowledgeFileError):
    pass


class FileExistsConflictError(KnowledgeFileError):
    pass


class FileMissingError(KnowledgeFileError):
    pass


@dataclass(frozen=True)
class SourceFileInfo:
    name: str
    title: str
    format: str
    size_bytes: int
    modified_at: datetime
    supported: bool
    conversion_status: str  # converted | outdated | not_converted | unsupported
    document: str | None


@dataclass(frozen=True)
class DocumentInfo:
    name: str
    size_bytes: int
    modified_at: datetime
    converted_at: datetime | None
    source_file: str | None
    in_index: bool


def _timestamp(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class KnowledgeFilesService:
    def __init__(self, sources_dir: Path, knowledge_dir: Path, max_upload_bytes: int) -> None:
        self._sources_dir = sources_dir
        self._knowledge_dir = knowledge_dir
        self._max_upload_bytes = max_upload_bytes

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    # --- sources -------------------------------------------------------------------------

    def list_sources(self) -> list[SourceFileInfo]:
        links = self._documents_by_source()
        items: list[SourceFileInfo] = []
        for path in self._iter_source_files():
            relative = path.relative_to(self._sources_dir).as_posix()
            document = links.get(relative)
            items.append(
                SourceFileInfo(
                    name=relative,
                    title=path.stem,
                    format=path.suffix.lower().lstrip("."),
                    size_bytes=path.stat().st_size,
                    modified_at=_timestamp(path),
                    supported=path.suffix.lower() in SUPPORTED_EXTENSIONS,
                    conversion_status=self._conversion_status(path, document),
                    document=document.relative_to(self._knowledge_dir).as_posix() if document else None,
                )
            )
        return items

    def save_upload(self, filename: str, stream: BinaryIO, *, overwrite: bool = False) -> SourceFileInfo:
        name = self._validate_upload_name(filename)
        target = self._sources_dir / name
        if target.exists() and not overwrite:
            raise FileExistsConflictError(f"Файл «{name}» уже есть в базе знаний")

        self._sources_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        fd, tmp_name = tempfile.mkstemp(dir=self._sources_dir, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                while chunk := stream.read(UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > self._max_upload_bytes:
                        limit_mb = self._max_upload_bytes // (1024 * 1024)
                        raise FileTooLargeError(f"Файл больше {limit_mb} МБ")
                    tmp.write(chunk)
            if written == 0:
                raise InvalidFileError("Файл пустой")
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

        logger.info("Admin uploaded source %s (%d bytes, overwrite=%s)", name, written, overwrite)
        return self._source_info(name)

    def delete_source(self, name: str) -> list[str]:
        """Delete a source file and its converted document. Returns deleted relative paths."""
        path = self._resolve_source(name)
        document = self._documents_by_source().get(path.relative_to(self._sources_dir).as_posix())

        deleted = [f"knowledge_sources/{name}"]
        path.unlink()
        if document is not None and document.exists():
            document.unlink()
            deleted.append(f"knowledge/{document.relative_to(self._knowledge_dir).as_posix()}")

        logger.info("Admin deleted %s", ", ".join(deleted))
        return deleted

    def convert_source(self, name: str) -> ConvertResult:
        path = self._resolve_source(name)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise InvalidFileError(f"Формат {path.suffix} не поддерживается конвертером")
        result = self._convert(path, force=True)
        logger.info("Admin converted %s: %s %s", name, result.status, result.message)
        return result

    def convert_pending(self) -> list[ConvertResult]:
        """Convert sources that are new or changed since the last conversion."""
        results = [
            self._convert(path, force=False)
            for path in self._iter_source_files()
            if path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        changed = [r for r in results if r.status != "skipped"]
        logger.info("Admin converted pending sources: %d changed of %d", len(changed), len(results))
        return results

    # --- documents -----------------------------------------------------------------------

    def list_documents(self, indexed: set[str]) -> list[DocumentInfo]:
        items: list[DocumentInfo] = []
        for path in self._iter_documents():
            header = read_header_fields(path)
            items.append(
                DocumentInfo(
                    name=path.relative_to(self._knowledge_dir).as_posix(),
                    size_bytes=path.stat().st_size,
                    modified_at=_timestamp(path),
                    converted_at=_parse_iso(header.get("converted-at")),
                    source_file=header.get("source-file"),
                    in_index=path.name in indexed,
                )
            )
        return items

    # --- internals -----------------------------------------------------------------------

    def _iter_source_files(self) -> list[Path]:
        if not self._sources_dir.exists():
            return []
        return sorted(
            p
            for p in self._sources_dir.rglob("*")
            if p.is_file() and not p.name.startswith(".") and not is_service_file(p)
        )

    def _iter_documents(self) -> list[Path]:
        if not self._knowledge_dir.exists():
            return []
        return sorted(
            p
            for p in self._knowledge_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in INDEXED_SUFFIXES and not is_service_file(p)
        )

    def _documents_by_source(self) -> dict[str, Path]:
        """Map source relative path → converted document, using the ``source-file`` header."""
        links: dict[str, Path] = {}
        for document in self._iter_documents():
            source_name = read_header_fields(document).get("source-file")
            if not source_name:
                continue
            relative_dir = document.parent.relative_to(self._knowledge_dir)
            links.setdefault((relative_dir / source_name).as_posix(), document)
        return links

    def _conversion_status(self, source: Path, document: Path | None) -> str:
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return "unsupported"
        if document is None:
            return "not_converted"
        if source.stat().st_mtime > document.stat().st_mtime:
            return "outdated"
        return "converted"

    def _convert(self, source: Path, *, force: bool) -> ConvertResult:
        relative = source.relative_to(self._sources_dir).as_posix()
        linked = self._documents_by_source().get(relative)
        return convert_file(source, self._sources_dir, self._knowledge_dir, force=force, output=linked)

    def _source_info(self, name: str) -> SourceFileInfo:
        return next(item for item in self.list_sources() if item.name == name)

    def _resolve_source(self, name: str) -> Path:
        root = self._sources_dir.resolve()
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise InvalidFileError("Недопустимое имя файла")
        if not candidate.is_file() or is_service_file(candidate) or candidate.name.startswith("."):
            raise FileMissingError(f"Файл «{name}» не найден")
        return candidate

    @staticmethod
    def _validate_upload_name(filename: str) -> str:
        name = Path((filename or "").replace("\\", "/")).name.strip()
        if not name or name.startswith(".") or len(name) > MAX_FILENAME_LENGTH:
            raise InvalidFileError("Недопустимое имя файла")
        if any(ord(ch) < 32 for ch in name):
            raise InvalidFileError("Имя файла содержит управляющие символы")
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS))
            raise InvalidFileError(f"Формат {suffix or 'без расширения'} не поддерживается. Допустимо: {allowed}")
        if is_service_file(Path(name)):
            raise InvalidFileError("Служебные файлы README загружать нельзя")
        return name
