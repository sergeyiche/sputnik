"""Checks for an organization owner's mailbox."""

from __future__ import annotations

import re

_EMAIL = re.compile(
    r"^[a-z0-9._%+\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$"
)


def valid_email(value: str) -> bool:
    email = value.strip().lower()
    return bool(email) and len(email) <= 200 and _EMAIL.fullmatch(email) is not None
