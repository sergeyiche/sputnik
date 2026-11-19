"""Deterministic contact extraction for the ``contact-request`` workflow.

The LLM must not decide whether a message contains a contact or invent a phone
number. These rules are intentionally conservative: a false positive sends a
letter with garbage to the fund, a false negative just asks one more question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from packages.platform.emails import valid_email

CHANNELS = ("phone", "email", "telegram", "max")

_PHONE_11 = re.compile(
    r"(?<![\d+])(?:\+7|8|7)[\s\-()]*(\d{3})[\s\-()]*(\d{3})[\s\-()]*(\d{2})[\s\-()]*(\d{2})(?!\d)"
)
_PHONE_10 = re.compile(
    r"(?<!\d)(\d{3})[\s\-()]*(\d{3})[\s\-()]*(\d{2})[\s\-()]*(\d{2})(?!\d)"
)
_PHONE_CONTEXT = re.compile(
    r"(?:телефон|тел\.?|позвон|связаться|свяжитесь|номер|whatsapp|ватсап|мобильн)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TELEGRAM_LINK = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{2,31})",
    re.IGNORECASE,
)
_MAX_LINK = re.compile(
    r"(?:https?://)?max\.ru/([A-Za-z0-9_.\-]{2,64})",
    re.IGNORECASE,
)
_USERNAME = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{2,31})")
_TELEGRAM_WORD = re.compile(r"(?:телеграм|telegram|телега|\bтг\b|\btg\b)", re.IGNORECASE)
_MAX_WORD = re.compile(r"(?:\bмакс\b|\bmax\b|мессенджер\s+max)", re.IGNORECASE)

_NAME = re.compile(
    r"(?:меня\s+зовут|моё\s+имя|мое\s+имя|зовут\s+меня|фамилия\s+имя)\s+"
    r"([А-ЯЁA-Z][а-яёa-z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-z\-]+){0,2})",
    re.IGNORECASE,
)
_ORG = re.compile(
    r"(?:представляю|от\s+лица|от\s+имени|я\s+из|мы\s+из)\s+"
    r"([«\"']?[А-ЯЁA-Z][\w«»\"'\s.\-]{1,80})",
    re.IGNORECASE,
)
_ORG_PREFIX = re.compile(
    r"^(?:компани[юи]|организаци[юи]|фонд[ау]?|клиник[уи]|центр[ау]?)\s+",
    re.IGNORECASE,
)
_ORG_LEGAL = re.compile(
    r"\b(ООО|АО|ПАО|ИП|НКО|АНО)\s+([«\"'][^»\"'\n]{1,80}[»\"']|[А-ЯЁA-Z][\w\-]{1,40})",
    re.IGNORECASE,
)
_ORG_NAMED = re.compile(
    r"\b(?:компания|организация|фонд|клиника|центр)\s+[«\"'][^»\"'\n]{2,80}[»\"']",
    re.IGNORECASE,
)
_GENERIC_ORG = {
    "клиника",
    "клиники",
    "клинику",
    "компания",
    "компании",
    "компанию",
    "организация",
    "организации",
    "организацию",
    "фонд",
    "фонда",
    "центр",
    "центра",
    "больница",
    "больницы",
}

_PARTNERSHIP = (
    "сотруднич",
    "партнёр",
    "партнер",
    "коллаборац",
    "спонсор",
    "грант",
    "совместн",
    "коммерческ",
    "инвестиц",
    "меценат",
    "предложени",
)
_ORGANIZATION = (
    "организац",
    "компани",
    "ооо",
    " ао ",
    " ип ",
    "фонд",
    "клиник",
    "учрежден",
    "ассоциац",
    "представляю",
    "от лица",
)
_CAREGIVER = (
    "мама",
    "папа",
    "муж",
    "жена",
    "сын",
    "дочь",
    "брат",
    "сестра",
    "родственник",
    "ухажива",
    "опекун",
    "близк",
)
_MEDICAL = (
    "паркинсон",
    "болезн",
    "диагноз",
    "симптом",
    "леводоп",
    "врач",
    "лекарств",
    "невролог",
)
_CANCEL = re.compile(
    r"^\s*(?:нет[,;]?\s*)?(?:отмена|не\s+надо|не\s+нужно|не\s+отправляй\w*|стоп|передумал\w*)"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)
_CONFIRM = re.compile(
    r"^(?:(?:да\s*[,;]?\s*)?"
    r"(?:верно|всё\s+верно|все\s+верно|подтверждаю|отправляйте|передавайте|давайте)|да)"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContactSlots:
    name: str = ""
    phone: str = ""
    email: str = ""
    telegram: str = ""
    max: str = ""
    handle: str = ""
    channel_hint: str = ""
    organization: str = ""
    kind: str = "other"
    topic: str = ""
    preferred_channel: str = ""

    @property
    def contact_channel(self) -> str:
        for channel in CHANNELS:
            if getattr(self, channel):
                return channel
        return ""

    @property
    def has_contact(self) -> bool:
        return bool(self.contact_channel)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "telegram": self.telegram,
            "max": self.max,
            "handle": self.handle,
            "organization": self.organization,
            "kind": self.kind,
            "topic": self.topic,
            "preferred_channel": self.preferred_channel,
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> "ContactSlots":
        data = raw if isinstance(raw, dict) else {}

        def value(key: str) -> str:
            return str(data.get(key) or "").strip()

        return cls(
            name=value("name"),
            phone=value("phone"),
            email=value("email"),
            telegram=value("telegram"),
            max=value("max"),
            handle=value("handle"),
            organization=value("organization"),
            kind=value("kind") or "other",
            topic=value("topic"),
            preferred_channel=value("preferred_channel"),
        )


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) != 10:
        return None
    if digits in {"103", "112", "911"}:
        return None
    if digits.startswith("0"):
        return None
    return f"+7{digits}"


def _find_phone(text: str) -> str:
    for match in _PHONE_11.finditer(text):
        phone = normalize_phone("".join(match.groups()))
        if phone:
            return phone
    if _PHONE_CONTEXT.search(text):
        for match in _PHONE_10.finditer(text):
            phone = normalize_phone("".join(match.groups()))
            if phone:
                return phone
    return ""


def _clean_name(value: str) -> str:
    return " ".join(value.strip(" .,!?;:«»\"'").split())[:80]


def _clean_org(value: str) -> str:
    text = " ".join(value.strip(" .,!?;:\n").split())
    if len(text) >= 2 and text[0] in "«\"“" and text[-1] in "»\"”":
        text = text[1:-1].strip()
    text = _ORG_PREFIX.sub("", text)
    return text[:120]


def classify_kind(text: str, *, has_organization: bool = False) -> str:
    lowered = f" {text.casefold().replace('ё', 'е')} "
    if any(word in lowered for word in _PARTNERSHIP):
        return "partnership"
    if has_organization or any(word in lowered for word in _ORGANIZATION):
        return "organization"
    if any(word in lowered for word in _CAREGIVER):
        return "caregiver"
    if any(word in lowered for word in _MEDICAL):
        return "patient"
    return "patient"


def _preferred_channel(text: str, slots: ContactSlots) -> str:
    lowered = text.casefold()
    if "позвон" in lowered or "набери" in lowered:
        return "phone" if slots.phone else slots.contact_channel
    if "на почт" in lowered or "email" in lowered or "e-mail" in lowered:
        return "email" if slots.email else slots.contact_channel
    if _TELEGRAM_WORD.search(text):
        return "telegram" if slots.telegram else slots.contact_channel
    if _MAX_WORD.search(text):
        return "max" if slots.max else slots.contact_channel
    return slots.contact_channel


def extract_contacts(text: str) -> ContactSlots:
    raw = (text or "").strip()
    lowered = raw.casefold().replace("ё", "е")
    phone = _find_phone(raw)

    email = ""
    for match in _EMAIL.finditer(raw):
        candidate = match.group(0).strip(".,;:()[]<>")
        if valid_email(candidate):
            email = candidate.lower()
            break

    telegram = ""
    max_contact = ""
    handle = ""

    tg_link = _TELEGRAM_LINK.search(raw)
    if tg_link:
        telegram = f"@{tg_link.group(1)}"
    max_link = _MAX_LINK.search(raw)
    if max_link:
        max_contact = f"@{max_link.group(1).lstrip('@')}"

    usernames = [f"@{m.group(1)}" for m in _USERNAME.finditer(raw)]
    if usernames and not telegram and not max_contact:
        candidate = usernames[0]
        if _TELEGRAM_WORD.search(raw) and not _MAX_WORD.search(raw):
            telegram = candidate
        elif _MAX_WORD.search(raw) and not _TELEGRAM_WORD.search(raw):
            max_contact = candidate
        elif _TELEGRAM_WORD.search(raw) and _MAX_WORD.search(raw):
            telegram = candidate
        else:
            handle = candidate

    # A MAX username can be given as a phone-like identifier; keep it readable.
    if _MAX_WORD.search(raw) and phone and not max_contact:
        max_contact = phone

    name_match = _NAME.search(raw)
    name = _clean_name(name_match.group(1)) if name_match else ""
    org_match = _ORG.search(raw)
    organization = _clean_org(org_match.group(1)) if org_match else ""
    if not organization:
        legal = _ORG_LEGAL.search(raw)
        if legal:
            organization = _clean_org(f"{legal.group(1)} {legal.group(2)}")
    if not organization:
        named = _ORG_NAMED.search(raw)
        if named:
            organization = _clean_org(named.group(0))
    if organization.casefold() in _GENERIC_ORG:
        organization = ""

    channel_hint = ""
    if _TELEGRAM_WORD.search(raw):
        channel_hint = "telegram"
    elif _MAX_WORD.search(raw):
        channel_hint = "max"

    slots = ContactSlots(
        name=name,
        phone=phone,
        email=email,
        telegram=telegram,
        max=max_contact,
        handle=handle,
        channel_hint=channel_hint,
        organization=organization,
        kind=classify_kind(raw, has_organization=bool(organization)),
        topic=" ".join(raw.split())[:180],
    )
    return replace(slots, preferred_channel=_preferred_channel(raw, slots))


def merge_contacts(base: ContactSlots, new: ContactSlots) -> ContactSlots:
    def pick(first: str, second: str) -> str:
        return second.strip() or first.strip()

    handle = pick(base.handle, new.handle)
    telegram = pick(base.telegram, new.telegram)
    max_contact = pick(base.max, new.max)
    hint = new.channel_hint or base.channel_hint
    if handle and not telegram and not max_contact:
        if hint == "telegram":
            telegram, handle = handle, ""
        elif hint == "max":
            max_contact, handle = handle, ""

    strong = {"partnership", "organization", "caregiver"}
    kind = base.kind if base.kind in strong else (new.kind or base.kind or "patient")
    merged = ContactSlots(
        name=pick(base.name, new.name),
        phone=pick(base.phone, new.phone),
        email=pick(base.email, new.email),
        telegram=telegram,
        max=max_contact,
        handle=handle,
        channel_hint=hint,
        organization=pick(base.organization, new.organization),
        kind=kind,
        topic=pick(base.topic, new.topic),
        preferred_channel=new.preferred_channel or base.preferred_channel,
    )
    return replace(merged, preferred_channel=merged.preferred_channel or merged.contact_channel)


def is_cancel(text: str, phrases: list[str] | None = None) -> bool:
    if _CANCEL.fullmatch(text or "") is not None:
        return True
    value = " ".join((text or "").casefold().split()).strip(" .!")
    return bool(phrases) and any(
        value == " ".join(str(phrase).casefold().split()).strip(" .!") for phrase in phrases or []
    )


def is_confirm(text: str, phrases: list[str] | None = None) -> bool:
    if _CONFIRM.fullmatch(text or "") is not None:
        return True
    value = " ".join((text or "").casefold().split()).strip(" .!")
    return bool(phrases) and any(
        value == " ".join(str(phrase).casefold().split()).strip(" .!") for phrase in phrases or []
    )


def resolve_handle(slots: ContactSlots, text: str) -> ContactSlots:
    """Turn a pending ``@username`` into Telegram/MAX after a clarification."""
    if not slots.handle or slots.telegram or slots.max:
        return slots
    lowered = (text or "").casefold()
    if _TELEGRAM_WORD.search(lowered):
        return replace(slots, telegram=slots.handle, handle="", preferred_channel="telegram")
    if _MAX_WORD.search(lowered):
        return replace(slots, max=slots.handle, handle="", preferred_channel="max")
    return slots
