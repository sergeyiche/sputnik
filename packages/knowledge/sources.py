"""Map indexed knowledge documents to their original downloadable files.

The vector store indexes normalized ``.txt`` files from ``KNOWLEDGE_DIR``.
Each converted file starts with a ``# source-file: <name>`` header pointing to
the original (pdf, docx, xlsx…) in ``KNOWLEDGE_SOURCES_DIR``. An optional JSON
manifest overrides titles and controls what may be downloaded.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

HEADER_LINE_PREFIX = "# "
HEADER_SCAN_LINES = 5
INDEXED_SUFFIXES = (".txt", ".md")
DEFAULT_MANIFEST_PATH = "./config/knowledge_sources.json"
DEFAULT_DOWNLOAD_PREFIX = "/v1/knowledge/sources"


@dataclass(frozen=True)
class SourceDocument:
    document: str
    title: str
    file_name: str | None = None
    format: str | None = None
    size_bytes: int | None = None
    hidden: bool = False

    @property
    def downloadable(self) -> bool:
        return self.file_name is not None


@lru_cache(maxsize=512)
def _read_header_fields(path: str, mtime_ns: int) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for _ in range(HEADER_SCAN_LINES):
                line = handle.readline()
                if not line.startswith(HEADER_LINE_PREFIX) or ":" not in line:
                    break
                key, _, value = line[len(HEADER_LINE_PREFIX):].partition(":")
                if value.strip():
                    fields[key.strip()] = value.strip()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Cannot read header from %s: %s", path, exc)
    return fields


def read_header_fields(path: Path) -> dict[str, str]:
    """Return ``# key: value`` fields written by the converter (source-file, converted-at…)."""
    try:
        return _read_header_fields(str(path), path.stat().st_mtime_ns)
    except FileNotFoundError:
        return {}


class SourceCatalog:
    def __init__(
        self,
        knowledge_dir: Path,
        sources_dir: Path,
        manifest_path: Path | None = None,
        download_prefix: str = DEFAULT_DOWNLOAD_PREFIX,
    ) -> None:
        self._knowledge_dir = knowledge_dir
        self._sources_dir = sources_dir
        self._manifest_path = manifest_path
        self._download_prefix = download_prefix.rstrip("/")
        self._manifest: dict[str, dict] = {}
        self._manifest_mtime: float | None = None

    def resolve(self, document: str) -> SourceDocument:
        """Describe an indexed document by its file name (e.g. ``Сон.txt``)."""
        overrides = self._manifest_entry(document)
        indexed_path = self._find_indexed(document)

        original = self._original_path(indexed_path, overrides)
        if overrides.get("downloadable") is False:
            original = None

        default_title = original.stem if original else Path(document).stem
        title = str(overrides.get("title") or default_title).strip()

        if original is None:
            return SourceDocument(document=document, title=title, hidden=bool(overrides.get("hidden")))

        return SourceDocument(
            document=document,
            title=title,
            file_name=original.relative_to(self._sources_dir.resolve()).as_posix(),
            format=original.suffix.lower().lstrip(".") or None,
            size_bytes=original.stat().st_size,
            hidden=bool(overrides.get("hidden")),
        )

    def describe(self, document: str) -> dict:
        """JSON-ready source description including a download URL when allowed."""
        source = self.resolve(document)
        return {
            "source": source.document,
            "title": source.title,
            "url": self.download_url(source.file_name) if source.file_name else None,
            "format": source.format,
            "size_bytes": source.size_bytes,
        }

    def download_url(self, file_name: str) -> str:
        return f"{self._download_prefix}/{quote(file_name)}"

    def downloadable_path(self, file_name: str) -> Path | None:
        """Return the original file path only if an indexed document links to it."""
        sources_root = self._sources_dir.resolve()
        candidate = (sources_root / file_name).resolve()
        if not candidate.is_relative_to(sources_root) or not candidate.is_file():
            return None

        allowed = {
            doc.file_name
            for doc in (self.resolve(name) for name in self._indexed_documents())
            if doc.file_name and not doc.hidden
        }
        relative = candidate.relative_to(sources_root).as_posix()
        return candidate if relative in allowed else None

    def _indexed_documents(self) -> list[str]:
        if not self._knowledge_dir.exists():
            return []
        return sorted(
            {p.name for p in self._knowledge_dir.rglob("*") if p.suffix.lower() in INDEXED_SUFFIXES}
        )

    def _find_indexed(self, document: str) -> Path | None:
        direct = self._knowledge_dir / document
        if direct.is_file():
            return direct
        return next(self._knowledge_dir.rglob(Path(document).name), None)

    def _original_path(self, indexed_path: Path | None, overrides: dict) -> Path | None:
        sources_root = self._sources_dir.resolve()
        explicit = overrides.get("file")
        candidates: list[Path] = []

        if explicit:
            candidates.append(sources_root / str(explicit))
        elif indexed_path is not None:
            header_name = read_header_fields(indexed_path).get("source-file")
            if header_name:
                relative_dir = indexed_path.parent.relative_to(self._knowledge_dir)
                candidates.append(sources_root / relative_dir / header_name)
                candidates.append(sources_root / header_name)

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_relative_to(sources_root) and resolved.is_file():
                return resolved
        return None

    def _manifest_entry(self, document: str) -> dict:
        self._reload_manifest()
        entry = self._manifest.get(document)
        return entry if isinstance(entry, dict) else {}

    def _reload_manifest(self) -> None:
        if self._manifest_path is None:
            return
        try:
            mtime = self._manifest_path.stat().st_mtime
        except FileNotFoundError:
            self._manifest, self._manifest_mtime = {}, None
            return
        if mtime == self._manifest_mtime:
            return
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            documents = data.get("documents", {}) if isinstance(data, dict) else {}
            self._manifest = documents if isinstance(documents, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Invalid sources manifest %s: %s", self._manifest_path, exc)
            self._manifest = {}
        self._manifest_mtime = mtime


_catalog: SourceCatalog | None = None
_catalog_key: tuple[str, str, str] | None = None


def get_source_catalog() -> SourceCatalog:
    global _catalog, _catalog_key
    key = (
        os.getenv("KNOWLEDGE_DIR", "./data/knowledge"),
        os.getenv("KNOWLEDGE_SOURCES_DIR", "./data/knowledge_sources"),
        os.getenv("KNOWLEDGE_SOURCES_MANIFEST", DEFAULT_MANIFEST_PATH),
    )
    if _catalog is None or _catalog_key != key:
        _catalog = SourceCatalog(
            knowledge_dir=Path(key[0]),
            sources_dir=Path(key[1]),
            manifest_path=Path(key[2]),
        )
        _catalog_key = key
    return _catalog
