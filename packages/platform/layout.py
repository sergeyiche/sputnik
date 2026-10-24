"""Per-organization directories. Bodies of documents stay in these folders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.platform.host import valid_slug


class UnsafeSlugError(ValueError):
    pass


@dataclass(frozen=True)
class TenantLayout:
    root: Path
    knowledge_dir: Path
    sources_dir: Path
    prompts_dir: Path
    branding_dir: Path
    prompt_file: Path

    def ensure(self) -> None:
        for path in (self.knowledge_dir, self.sources_dir, self.prompts_dir, self.branding_dir):
            path.mkdir(parents=True, exist_ok=True)

    def disk_bytes(self) -> int:
        total = 0
        if not self.root.exists():
            return 0
        for path in self.root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def document_count(self) -> int:
        if not self.knowledge_dir.exists():
            return 0
        return sum(1 for path in self.knowledge_dir.glob("*.txt") if path.is_file())


def layout_for(data_root: Path, slug: str) -> TenantLayout:
    if not valid_slug(slug):
        raise UnsafeSlugError(slug)
    root = (data_root / "tenants" / slug).resolve()
    base = (data_root / "tenants").resolve()
    if root != base and base not in root.parents:
        raise UnsafeSlugError(slug)
    return TenantLayout(
        root=root,
        knowledge_dir=root / "knowledge",
        sources_dir=root / "knowledge_sources",
        prompts_dir=root / "prompts",
        branding_dir=root / "branding",
        prompt_file=root / "prompt_default.md",
    )
