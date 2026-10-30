"""Russian phone numbers stored as +7 and ten digits."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D")


def normalize_phone(value: str) -> str:
    digits = _DIGITS.sub("", value or "")
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Укажите российский номер телефона")
    return "+7" + digits
