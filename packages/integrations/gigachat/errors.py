"""Detect GigaChat / HTTP auth failures for retry-after-refresh."""

from __future__ import annotations


def is_gigachat_auth_error(exc: BaseException) -> bool:
    """True if the exception indicates an expired/invalid access token (HTTP 401)."""
    try:
        from gigachat.exceptions import AuthenticationError

        if isinstance(exc, AuthenticationError):
            return True
    except ImportError:
        pass

    status = getattr(exc, "status_code", None)
    if status == 401:
        return True

    text = str(exc).lower()
    return "401" in text and ("unauthorized" in text or "authentication" in text or "auth" in text)
