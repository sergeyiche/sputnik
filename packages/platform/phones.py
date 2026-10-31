"""Russian phone numbers stored as +7 and ten digits."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D")


def normalize_phone(value: str) -> str:
    digits = _DIGITS.sub("", value or "")
    if len(digits) >= 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) >= 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Укажите российский номер телефона")
    return "+7" + digits


def format_phone(value: str) -> str:
    """Show a stored number as ``+7 900 000-00-00``."""
    national = normalize_phone(value)[2:]
    return f"+7 {national[:3]} {national[3:6]}-{national[6:8]}-{national[8:]}"
