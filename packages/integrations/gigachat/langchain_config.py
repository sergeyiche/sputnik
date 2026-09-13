"""Shared helpers for LangChain GigaChat client configuration."""

from __future__ import annotations

import os

from packages.integrations.gigachat.ssl import resolve_ca_bundle_file


def get_gigachat_ca_bundle_file() -> str | None:
    explicit = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip() or None
    resolved = resolve_ca_bundle_file(explicit)
    return str(resolved) if resolved else None


def get_gigachat_verify_ssl_certs() -> bool:
    return os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "true").lower() == "true"
