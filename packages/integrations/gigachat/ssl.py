"""TLS/SSL configuration for GigaChat HTTP clients."""

from __future__ import annotations

import ssl
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CA_DIR = PROJECT_ROOT / "config" / "certs"
DEFAULT_CA_BUNDLE = DEFAULT_CA_DIR / "gigachat-ca-bundle.pem"


def bundled_ca_paths() -> list[Path]:
    """Known certificate files shipped/downloaded for GigaChat."""
    names = (
        "gigachat-ca-bundle.pem",
        "russian_trusted_root_ca_pem.crt",
        "russian_trusted_sub_ca_pem.crt",
    )
    return [DEFAULT_CA_DIR / name for name in names]


def resolve_ca_bundle_file(explicit_path: str | None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.is_file():
            return path
        raise FileNotFoundError(f"GIGACHAT_CA_BUNDLE_FILE not found: {path}")

    for path in bundled_ca_paths():
        if path.is_file():
            return path
    return None


def build_merged_ca_bundle() -> Path | None:
    """Merge project certs with certifi bundle for httpx."""
    try:
        import certifi
    except ImportError:
        certifi = None

    parts: list[str] = []
    if certifi:
        parts.append(Path(certifi.where()).read_text(encoding="utf-8"))

    for path in bundled_ca_paths():
        if path.is_file() and path.name != DEFAULT_CA_BUNDLE.name:
            parts.append(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n"))

    if not parts:
        return None

    DEFAULT_CA_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CA_BUNDLE.write_text("\n".join(p.strip() for p in parts if p.strip()) + "\n", encoding="utf-8")
    return DEFAULT_CA_BUNDLE


def get_httpx_verify(*, verify_ssl_certs: bool, ca_bundle_file: str | None) -> bool | ssl.SSLContext:
    if not verify_ssl_certs:
        return False

    explicit = resolve_ca_bundle_file(ca_bundle_file)
    if explicit:
        return str(explicit)

    merged = build_merged_ca_bundle()
    if merged:
        return str(merged)

    # Last resort: certifi only (may still fail for GigaChat)
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return True
