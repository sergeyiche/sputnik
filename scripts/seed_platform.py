#!/usr/bin/env python3
"""Lay out tenant folders and rename the fund account to parkinson.

Owner email and password live in platform.db. This script does not read or
write them.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.platform.layout import layout_for
from packages.platform.store import PlatformStore

DATA = Path(os.getenv("DATA_ROOT", ROOT / "data"))
DB = Path(os.getenv("PLATFORM_DB_PATH", DATA / "platform" / "platform.db"))


def _copy_tree(source: Path, destination: Path, *, skip_names: set[str] | None = None) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    skipped = skip_names or set()
    for path in source.iterdir():
        if path.name in skipped or path.name.startswith("."):
            continue
        target = destination / path.name
        if path.is_dir():
            shutil.copytree(path, target, dirs_exist_ok=True)
        elif not target.exists():
            shutil.copy2(path, target)


def _rename_fund_dir() -> None:
    old = DATA / "tenants" / "fond"
    new = DATA / "tenants" / "parkinson"
    if old.exists() and not new.exists():
        old.rename(new)


def main() -> None:
    _rename_fund_dir()
    store = PlatformStore(DB)
    store.rename_slug("fond", "parkinson")

    fund = layout_for(DATA, "parkinson")
    fund.ensure()
    _copy_tree(ROOT / "data" / "knowledge", fund.knowledge_dir, skip_names={"sample.txt"})
    _copy_tree(ROOT / "data" / "knowledge_sources", fund.sources_dir, skip_names={"README.md"})
    _copy_tree(ROOT / "data" / "prompts", fund.prompts_dir)

    help_layout = layout_for(DATA, "help")
    help_layout.ensure()
    letter = ROOT / "Спутник.Главное.docx"
    if letter.exists():
        target = help_layout.sources_dir / letter.name
        if not target.exists():
            shutil.copy2(letter, target)
    print(f"platform db: {DB}")
    print("tenants: parkinson, help")


if __name__ == "__main__":
    main()
