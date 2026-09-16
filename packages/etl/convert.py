"""Convert raw knowledge sources (pdf, docx, xlsx, txt, md) to normalized .txt files."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.etl.converters import SUPPORTED_EXTENSIONS, ConversionError, EXTRACTORS


@dataclass
class ConvertResult:
    source: Path
    output: Path
    status: str  # created | updated | skipped | failed
    message: str = ""


def _build_output_path(source: Path, sources_root: Path, output_root: Path) -> Path:
    relative = source.relative_to(sources_root)
    return output_root / relative.with_suffix(".txt")


def _resolve_sources_root(source: Path, sources_dir: Path) -> Path:
    """Keep mirror path under sources_dir when possible; else use file's parent."""
    source = source.resolve()
    sources_dir = sources_dir.resolve()
    try:
        source.relative_to(sources_dir)
        return sources_dir
    except ValueError:
        return source.parent


def _format_output(source: Path, text: str) -> str:
    converted_at = datetime.now(UTC).isoformat()
    header = (
        f"# source-file: {source.name}\n"
        f"# source-format: {source.suffix.lower().lstrip('.')}\n"
        f"# converted-at: {converted_at}\n\n"
    )
    body = text.strip()
    return f"{header}{body}\n"


def convert_file(
    source: Path,
    sources_root: Path,
    output_root: Path,
    *,
    force: bool = False,
) -> ConvertResult:
    output = _build_output_path(source, sources_root, output_root)
    output.parent.mkdir(parents=True, exist_ok=True)

    if (
        not force
        and output.exists()
        and output.stat().st_mtime >= source.stat().st_mtime
    ):
        return ConvertResult(source=source, output=output, status="skipped")

    extractor = EXTRACTORS.get(source.suffix.lower())
    if extractor is None:
        return ConvertResult(
            source=source,
            output=output,
            status="failed",
            message=f"Unsupported extension: {source.suffix}",
        )

    try:
        text = extractor(source)
        if not text.strip():
            return ConvertResult(
                source=source,
                output=output,
                status="failed",
                message="Empty text after conversion",
            )
        existed = output.exists()
        output.write_text(_format_output(source, text), encoding="utf-8")
        status = "updated" if existed else "created"
        return ConvertResult(source=source, output=output, status=status)
    except (ConversionError, OSError) as exc:
        return ConvertResult(source=source, output=output, status="failed", message=str(exc))


def convert_paths(
    paths: list[str | Path],
    output_dir: str | Path,
    *,
    sources_dir: str | Path | None = None,
    force: bool = False,
) -> list[ConvertResult]:
    """Convert one or more explicit source files."""
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    default_sources = Path(
        sources_dir
        if sources_dir is not None
        else os.getenv("KNOWLEDGE_SOURCES_DIR", "./data/knowledge_sources")
    )

    results: list[ConvertResult] = []
    for raw in paths:
        source = Path(raw).expanduser().resolve()
        if not source.is_file():
            results.append(
                ConvertResult(
                    source=source,
                    output=output_root / f"{source.name}.txt",
                    status="failed",
                    message="File not found",
                )
            )
            continue
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            results.append(
                ConvertResult(
                    source=source,
                    output=output_root / source.with_suffix(".txt").name,
                    status="failed",
                    message=f"Unsupported extension: {source.suffix}",
                )
            )
            continue
        sources_root = _resolve_sources_root(source, default_sources)
        results.append(convert_file(source, sources_root, output_root, force=force))
    return results


def convert_sources(
    sources_dir: str | Path,
    output_dir: str | Path,
    *,
    force: bool = False,
) -> list[ConvertResult]:
    sources_root = Path(sources_dir)
    output_root = Path(output_dir)

    if not sources_root.exists():
        raise FileNotFoundError(f"Sources directory not found: {sources_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    results: list[ConvertResult] = []
    for path in sorted(sources_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        results.append(convert_file(path, sources_root, output_root, force=force))

    return results


def _print_summary(results: list[ConvertResult]) -> int:
    created = sum(1 for r in results if r.status == "created")
    updated = sum(1 for r in results if r.status == "updated")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = [r for r in results if r.status == "failed"]

    for result in results:
        if result.status == "failed":
            print(f"✗ {result.source}: {result.message}")
        elif result.status != "skipped":
            print(f"✓ {result.source} → {result.output}")

    print(
        f"\nConversion summary: created={created}, updated={updated}, "
        f"skipped={skipped}, failed={len(failed)}"
    )
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert raw knowledge sources to normalized UTF-8 .txt files. "
            "Pass file paths to convert individually, or omit them to scan --sources."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional file path(s) to convert (pdf, docx, xlsx, txt, md)",
    )
    parser.add_argument(
        "--sources",
        default=os.getenv("KNOWLEDGE_SOURCES_DIR", "./data/knowledge_sources"),
        help="Directory with raw files when converting the whole tree (default)",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("KNOWLEDGE_DIR", "./data/knowledge"),
        help="Directory for converted .txt files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even if output is up to date",
    )
    args = parser.parse_args()

    if args.paths:
        results = convert_paths(
            args.paths,
            args.output,
            sources_dir=args.sources,
            force=args.force,
        )
    else:
        results = convert_sources(args.sources, args.output, force=args.force)

    if not results:
        target = ", ".join(args.paths) if args.paths else str(args.sources)
        print(f"No supported files found in {target}")
        print(f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return

    exit_code = _print_summary(results)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
