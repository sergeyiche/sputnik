"""Format-specific text extractors for knowledge base sources."""

from __future__ import annotations

from pathlib import Path


class ConversionError(Exception):
    """Raised when a source file cannot be converted."""


def extract_txt(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ConversionError(f"Cannot decode text file: {path}")


def extract_md(path: Path) -> str:
    return extract_txt(path)


def extract_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise ConversionError("python-docx is not installed") from exc

    document = Document(str(path))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ConversionError("pypdf is not installed") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"--- Page {index} ---\n{text}")
    if not pages:
        raise ConversionError(f"No extractable text in PDF: {path}")
    return "\n\n".join(pages)


EXTRACTORS = {
    ".txt": extract_txt,
    ".md": extract_md,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
}

SUPPORTED_EXTENSIONS = frozenset(EXTRACTORS)
