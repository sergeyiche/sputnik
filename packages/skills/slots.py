"""Deterministic slot handling for skills that must ask before answering.

A domain skill can declare ``config.clarify``::

    {"slot": "city", "question": "...", "source": "Список врачей.txt"}

If the slot is missing from the user's message and from dialogue memory, the
assistant asks for it instead of replying "no information". If the slot is
present, the value is added to the search query so retrieval sees it even when
the value came from an earlier turn.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from packages.knowledge.base import RetrievedChunk
from packages.knowledge.policy import sanitize_context_text
from packages.skills.models import MatchedSkill, Skill

DEFAULT_CITY_QUESTION = (
    "Конечно, помогу подобрать специалиста. Подскажите, пожалуйста, "
    "в каком городе вы находитесь? Я найду врачей-паркинсонологов рядом с вами."
)
DEFAULT_CITY_NOT_FOUND = (
    "В списке фонда пока нет врачей из города {city}. "
    "Подскажите другой город — я посмотрю, кто есть рядом."
)
DEFAULT_CITY_SOURCE = "Список врачей паркинсонологов.txt"
DEFAULT_CITY_SOURCES = (
    "Список врачей паркинсонологов.txt",
    "Список врачей с сайта.txt",
    "Список врачей.txt",
)
DEFAULT_CITY_ROWS = 30

# Used only for the built-in find-doctor skill when an existing tenant row has
# no clarify config yet (older seeds).
DEFAULT_FIND_DOCTOR_CLARIFY = {
    "slot": "city",
    "question": DEFAULT_CITY_QUESTION,
    "source": DEFAULT_CITY_SOURCE,
    "sources": list(DEFAULT_CITY_SOURCES),
    "region_aliases": {
        "республика адыгея": "Майкоп",
        "адыгея": "Майкоп",
        "краснодарский край": "Краснодар",
    },
}

_CITY_ENDINGS = (
    "ого",
    "его",
    "ому",
    "ему",
    "ыми",
    "ими",
    "ами",
    "ями",
    "ах",
    "ях",
    "ом",
    "ем",
    "ой",
    "ей",
    "ую",
    "юю",
    "а",
    "я",
    "е",
    "и",
    "ы",
    "у",
    "ю",
    "о",
    "ь",
)
_DOCTOR_INTENT = re.compile(
    r"(?:врач|доктор|невролог|паркинсонолог|клиник|контакт|специалист|список|"
    r"к\s+кому|обратиться|где\s+найти|запис|приним|консультац)",
    re.IGNORECASE,
)
_NON_CITY_ANSWERS = {
    "спасибо",
    "да",
    "нет",
    "хорошо",
    "понятно",
    "ладно",
    "ок",
    "окей",
    "не знаю",
    "не важно",
    "без разницы",
    "не хочу",
}
_CITY_PREPOSITION = re.compile(
    r"(?:живу|проживаю|нахожусь)\s+в(?:\s+городе)?\s+"
    r"([А-ЯЁA-Z][А-ЯЁа-яёa-z\-]+(?:\s+[А-ЯЁA-Z][А-ЯЁа-яёa-z\-]+)?)"
    r"|(?:я\s+из|из(?:\s+города)?|в(?:\s+городе)?|город)\s+"
    r"([А-ЯЁA-Z][А-ЯЁа-яёa-z\-]+(?:\s+[А-ЯЁA-Z][А-ЯЁа-яёa-z\-]+)?)",
    re.UNICODE,
)

_rows_lock = threading.Lock()
_rows_cache: dict[tuple[str, int], tuple["DirectoryRow", ...]] = {}
_sections_cache: dict[tuple[str, int], tuple["DocumentSection", ...]] = {}
_SECTION_HEADING = re.compile(r"^\s*((?:\d+\.)+\d+)\.\s+(.+?)\s*$")
_SECTION_PAYMENT_MARKERS = ("оплата — перевод", "tbank.ru", "промокод")
_NUMBERED_ENTRY = re.compile(
    r"(?ms)^\s*(\d+)\)\s*(.*?)(?=^\s*\d+\)|\Z)"
)


@dataclass(frozen=True)
class DirectoryRow:
    city: str
    text: str


@dataclass(frozen=True)
class DocumentSection:
    title: str
    text: str


@dataclass(frozen=True)
class TurnAdjustment:
    """What the skill runtime should change before retrieval/generation."""

    clarification: str | None = None
    city: str | None = None
    skill: str = ""
    exact_chunks: tuple[RetrievedChunk, ...] = ()
    direct_answer: str | None = None


def city_stem(value: str) -> str:
    """Crude but stable normalizer for Russian city inflections.

    «Краснодаре» → «краснодар», «Казани» → «казан», «Москва»/«Москве» →
    «москв». It is intentionally not a full morphology engine; it only needs to
    make a memory value and a knowledge-base value meet.
    """
    text = value.casefold().replace("ё", "е").strip(" .,!?;:«»\"'()")
    text = re.sub(r"^(?:г\.?\s*|город\s+)", "", text)
    text = re.sub(r"\s+", " ", text)
    for ending in _CITY_ENDINGS:
        if text.endswith(ending) and len(text) - len(ending) >= 2:
            return text[: -len(ending)].strip()
    return text


def _looks_like_city_answer(text: str) -> bool:
    value = text.strip().strip(" .,!?;:")
    if not value or "?" in text:
        return False
    if value.casefold() in _NON_CITY_ANSWERS:
        return False
    words = value.split()
    if not 1 <= len(words) <= 3:
        return False
    return all(
        re.fullmatch(r"[А-ЯЁA-Z][А-ЯЁа-яёa-z\-]+", word) is not None for word in words
    )


def _term_present(term: str, text: str) -> bool:
    """Loose word match for Russian inflections («команда»/«команду»)."""
    normalized = text.casefold().replace("ё", "е")
    words = [word for word in term.casefold().replace("ё", "е").split() if word]
    return bool(words) and all(city_stem(word) in normalized for word in words)


def _city_from_preposition(text: str) -> str | None:
    for match in _CITY_PREPOSITION.finditer(text):
        value = (match.group(1) or match.group(2) or "").strip()
        if value:
            return value
    return None


def _same_city(left: str, right: str) -> bool:
    left_stem, right_stem = city_stem(left), city_stem(right)
    if left_stem == right_stem:
        return True
    if min(len(left_stem), len(right_stem)) < 3:
        return False
    return (
        abs(len(left_stem) - len(right_stem)) <= 2
        and SequenceMatcher(None, left_stem, right_stem).ratio() >= 0.82
    )


def extract_city(text: str, cities: list[str] | tuple[str, ...]) -> str | None:
    """Return a canonical city mentioned in ``text``.

    Known city stems are matched with up to three inflection letters, so both
    «Краснодар» and «в Краснодаре» work. A prepositional phrase is a fallback
    for names that the catalog does not contain.
    """
    if not text:
        return None
    normalized = text.casefold().replace("ё", "е")
    for city in sorted(cities, key=len, reverse=True):
        stem = city_stem(city)
        if len(stem) < 2:
            continue
        pattern = re.compile(
            rf"(?<![а-яa-z0-9]){re.escape(stem)}(?:[а-я]{{0,3}})?(?![а-яa-z0-9])"
        )
        if pattern.search(normalized):
            return city
    candidate = _city_from_preposition(text)
    if candidate:
        for city in cities:
            if _same_city(candidate, city):
                return city
        return candidate
    return None


def canonical_city(value: str | None, cities: list[str] | tuple[str, ...]) -> str | None:
    """Map a stored/memory value to the spelling used in the knowledge base."""
    if not value or not value.strip():
        return None
    for city in cities:
        if _same_city(value, city):
            return city
    return value.strip().strip(".,!?;:") or None


def _parse_rows(text: str) -> tuple[DirectoryRow, ...]:
    lines = text.splitlines()
    indexes = {"name": 0, "contact": 1, "city": 2, "center": 3, "specialty": 4}
    start = 0
    for index, line in enumerate(lines):
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) < 3:
            continue
        lowered = [column.casefold() for column in columns]
        if not any(column.startswith("фио") for column in lowered):
            continue
        if not any(column.startswith("город") for column in lowered):
            continue
        indexes = {
            "name": next((i for i, column in enumerate(lowered) if column.startswith("фио")), 0),
            "contact": next((i for i, column in enumerate(lowered) if "контакт" in column), 1),
            "city": next((i for i, column in enumerate(lowered) if column.startswith("город")), 2),
            "center": next(
                (i for i, column in enumerate(lowered) if "центр" in column or "клиник" in column),
                3,
            ),
            "specialty": next(
                (i for i, column in enumerate(lowered) if "специализ" in column),
                len(columns) - 1,
            ),
        }
        start = index + 1
        break

    rows: list[DirectoryRow] = []
    seen: set[tuple[str, str]] = set()
    for line in lines[start:]:
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) <= indexes["city"]:
            continue
        city = columns[indexes["city"]].strip()
        if not city or city.startswith("#") or city.startswith("---"):
            continue
        values = [
            columns[position]
            for position in indexes.values()
            if position < len(columns) and columns[position]
        ]
        if not values:
            continue
        text = " | ".join(values)
        key = (city.casefold(), text.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(DirectoryRow(city=city, text=text))
    return tuple(rows)


def load_rows(path: Path | None) -> list[DirectoryRow]:
    """Parse a tabular knowledge document into one row per record (cached by mtime)."""
    if path is None:
        return []
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_mtime_ns)
    with _rows_lock:
        cached = _rows_cache.get(key)
    if cached is not None:
        return list(cached)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows = _parse_rows(text)
    with _rows_lock:
        _rows_cache[key] = rows
        # Keep the cache bounded for long-running processes.
        if len(_rows_cache) > 32:
            _rows_cache.pop(next(iter(_rows_cache)))
    return list(rows)


def load_cities(path: Path | None) -> list[str]:
    """Unique city names from the same tabular document."""
    cities: dict[str, str] = {}
    for row in load_rows(path):
        cities.setdefault(city_stem(row.city), row.city)
    return sorted(cities.values(), key=len, reverse=True)


def _parse_sections(text: str) -> tuple[DocumentSection, ...]:
    sections: list[DocumentSection] = []
    current: DocumentSection | None = None
    for line in text.splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            if current is not None:
                sections.append(current)
            current = DocumentSection(title=f"{match.group(1)}. {match.group(2)}", text="")
            continue
        if current is not None:
            current = DocumentSection(title=current.title, text=f"{current.text}\n{line}".strip())
    if current is not None:
        sections.append(current)
    return tuple(sections)


def load_sections(path: Path | None) -> list[DocumentSection]:
    """Split a numbered knowledge document into its ``1.1``-style sections."""
    if path is None:
        return []
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_mtime_ns)
    with _rows_lock:
        cached = _sections_cache.get(key)
    if cached is not None:
        return list(cached)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    sections = _parse_sections(text)
    with _rows_lock:
        _sections_cache[key] = sections
        if len(_sections_cache) > 32:
            _sections_cache.pop(next(iter(_sections_cache)))
    return list(sections)


def _clean_section(text: str) -> str:
    cleaned = sanitize_context_text(text)
    lines = [
        line
        for line in cleaned.splitlines()
        if not any(marker in line.casefold() for marker in _SECTION_PAYMENT_MARKERS)
    ]
    return "\n".join(lines).strip()


def _project_summary(section: DocumentSection, limit: int = 220) -> str:
    body = _clean_section(section.text)
    if not body:
        return ""
    line = " ".join(body.split())
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return line


def project_lookup(
    skill: Skill,
    question: str,
    knowledge_dir: Path | None,
) -> TurnAdjustment | None:
    """Return a deterministic answer for a fund project or the full project menu."""
    config = skill.config if isinstance(skill.config, dict) else {}
    lookup = config.get("lookup")
    if not isinstance(lookup, dict):
        return None
    source = str(lookup.get("source") or "").strip()
    items = lookup.get("items")
    if not source or not isinstance(items, list):
        return None
    path = _source_path(knowledge_dir, source)
    sections = load_sections(path)
    if not sections:
        return None
    lowered = question.casefold()

    all_terms = [
        str(term).strip()
        for term in lookup.get("all_terms") or []
        if str(term).strip()
    ]
    if all_terms and any(_term_present(term, lowered) for term in all_terms):
        excluded = {
            str(value).casefold()
            for value in lookup.get("all_exclude") or []
            if str(value).strip()
        }
        selected = [
            section
            for section in sections
            if re.match(r"^1\.", section.title)
            and not any(marker in section.title.casefold() for marker in excluded)
        ]
        if selected:
            summary_limit = int(lookup.get("all_summary_chars") or 220)
            lines = ["В навигаторе фонда перечислены такие проекты:", ""]
            for section in selected:
                title = section.title.split(". ", 1)[-1]
                summary = _project_summary(section, summary_limit)
                lines.append(f"- **{title}**" + (f" — {summary}" if summary else ""))
            lines.extend(
                [
                    "",
                    "Подробнее о каждом проекте — на официальном сайте фонда "
                    "или в навигаторе по проектам.",
                ]
            )
            answer = "\n".join(lines)
            return TurnAdjustment(
                direct_answer=answer,
                exact_chunks=(
                    RetrievedChunk(
                        content=answer,
                        source=Path(source).name,
                        score=None,
                        metadata={"lookup": "project-all", "projects": len(selected)},
                    ),
                ),
                skill=skill.name,
            )

    for item in items:
        if not isinstance(item, dict):
            continue
        terms = [str(term) for term in item.get("terms") or [] if str(term).strip()]
        heading = str(item.get("heading") or "").strip().casefold()
        if not terms or not heading:
            continue
        if not any(_term_present(term, lowered) for term in terms):
            continue
        for section in sections:
            if heading not in section.title.casefold():
                continue
            body = _clean_section(section.text)
            if not body:
                continue
            if len(body) > 1200:
                body = body[:1200].rsplit("\n", 1)[0].rstrip() + "…"
            title = section.title.split(". ", 1)[-1]
            answer = (
                f"О проекте «{title}»:\n\n{body}\n\n"
                "Информация может обновляться — уточняйте на официальном сайте фонда."
            )
            return TurnAdjustment(
                direct_answer=answer,
                exact_chunks=(
                    RetrievedChunk(
                        content=f"Проект «{title}»:\n{body}",
                        source=Path(source).name,
                        score=None,
                        metadata={"lookup": "project", "project": title},
                    ),
                ),
                skill=skill.name,
            )
    return None


def _text_range(text: str, start: str, end: str) -> str:
    lowered = text.casefold()
    begin = lowered.find(start.casefold()) if start else 0
    if begin < 0:
        begin = 0
    finish = lowered.find(end.casefold(), begin + 1) if end else -1
    if finish < 0:
        finish = len(text)
    return text[begin:finish].strip()


def _numbered_entries(text: str) -> list[str]:
    entries: list[str] = []
    for match in _NUMBERED_ENTRY.finditer(text):
        body = " ".join(match.group(2).split()).strip(" ;")
        if body:
            entries.append(f"{match.group(1)}) {body}")
    return entries


def list_lookup(
    skill: Skill,
    question: str,
    knowledge_dir: Path | None,
) -> TurnAdjustment | None:
    """Return all numbered entries from a configured KB range.

    Used for DBS/FUS centres where a partial top-k answer is unacceptable.
    """
    config = skill.config if isinstance(skill.config, dict) else {}
    spec = config.get("list_lookup")
    if not isinstance(spec, dict):
        return None
    source = str(spec.get("source") or "").strip()
    raw_sections = spec.get("sections")
    if not source or not isinstance(raw_sections, list):
        return None
    path = _source_path(knowledge_dir, source)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lowered = question.casefold()
    selected: list[dict] = []
    for raw in raw_sections:
        if not isinstance(raw, dict):
            continue
        terms = [str(term) for term in raw.get("terms") or [] if str(term).strip()]
        if terms and any(_term_present(term, lowered) for term in terms):
            selected.append(raw)
    if not selected:
        all_terms = [
            str(term) for term in spec.get("all_terms") or [] if str(term).strip()
        ]
        if all_terms and any(_term_present(term, lowered) for term in all_terms):
            selected = [raw for raw in raw_sections if isinstance(raw, dict)]
    if not selected:
        return None
    blocks: list[str] = []
    chunk_parts: list[str] = []
    for raw in selected:
        title = str(raw.get("title") or "Список").strip()
        start = str(raw.get("start") or "").strip()
        end = str(raw.get("end") or "").strip()
        limit = max(1, min(int(raw.get("limit") or 20), 40))
        ranged = _text_range(text, start, end)
        entries = _numbered_entries(ranged)[:limit]
        body = "\n".join(entries) if entries else ranged[:4000]
        body = sanitize_context_text(body).strip()
        if not body:
            continue
        blocks.append(f"### {title}\n\n{body}")
        chunk_parts.append(f"{title}:\n{body}")
    if not blocks:
        return None
    answer = (
        "\n\n".join(blocks)
        + "\n\nИнформация может обновляться — уточняйте в клинике."
    )
    return TurnAdjustment(
        direct_answer=answer,
        exact_chunks=(
            RetrievedChunk(
                content="\n\n".join(chunk_parts),
                source=Path(source).name,
                score=None,
                metadata={"lookup": "list"},
            ),
        ),
        skill=skill.name,
    )

def city_context_chunks(
    rows: list[DirectoryRow],
    city: str,
    *,
    source: str,
    limit: int = DEFAULT_CITY_ROWS,
) -> list[RetrievedChunk]:
    """Build an exact, city-filtered context block for the retriever."""
    matches = [row for row in rows if _same_city(row.city, city)]
    if not matches:
        return []
    limit = max(1, min(int(limit), 60))
    shown = matches[:limit]
    lines = [f"Врачи в городе {city}:"]
    lines.extend(f"- {row.text}" for row in shown)
    if len(matches) > limit:
        lines.append(
            f"…Всего в списке {len(matches)} врачей в этом городе; "
            f"показаны первые {limit}. Уточните район или клинику."
        )
    return [
        RetrievedChunk(
            content="\n".join(lines),
            source=Path(source).name,
            score=None,
            metadata={"lookup": "city", "city": city, "rows": len(matches)},
        )
    ]


def city_answer(
    rows: list[DirectoryRow],
    city: str,
    *,
    limit: int = DEFAULT_CITY_ROWS,
) -> str | None:
    """A complete, deterministic list for a city lookup.

    Small models tend to keep only the rows whose specialty contains
    «паркинсонолог». For a fragile contact list the runtime composes the answer
    itself and the model is not involved.
    """
    matches = [row for row in rows if _same_city(row.city, city)]
    if not matches:
        return None
    limit = max(1, min(int(limit), 60))
    shown = matches[:limit]
    lines = [f"В городе {city} в списке фонда есть такие специалисты:", ""]
    seen_names: set[str] = set()
    for row in shown:
        parts = [part.strip() for part in row.text.split(" | ")]
        name = parts[0] if parts else ""
        contact = parts[1] if len(parts) > 1 else ""
        center = parts[3] if len(parts) > 3 else ""
        specialty = parts[4] if len(parts) > 4 else ""
        if not name:
            continue
        name_key = " ".join(name.casefold().replace("ё", "е").split())
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        line = f"- **{name}**"
        if specialty:
            line += f" — {specialty}"
        if contact:
            line += f"\n  Контакт: {contact}"
        if center:
            line += f"\n  Клиника: {center}"
        lines.append(line)
    if len(matches) > limit:
        lines.append(
            f"\n…Всего в списке {len(matches)} врачей в этом городе; "
            f"показаны первые {limit}. Уточните район или клинику."
        )
    lines.extend(
        [
            "",
            "Выбор специалиста — за вами и лечащим врачом. "
            "Условия приёма (ОМС/платно) уточняйте в клинике.",
        ]
    )
    return "\n".join(lines)


def clarification_config(skill: Skill) -> dict | None:
    """Explicit admin config, or the built-in default for the seeded skill."""
    if isinstance(skill.config, dict):
        raw = skill.config.get("clarify")
        if isinstance(raw, dict):
            return raw
    if skill.skill_type == "domain" and skill.name == "find-doctor":
        return dict(DEFAULT_FIND_DOCTOR_CLARIFY)
    return None


def _source_path(knowledge_dir: Path | None, source: str | None) -> Path | None:
    if knowledge_dir is None or not source:
        return None
    root = knowledge_dir.resolve()
    candidate = (root / source).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        return None
    return candidate


def _region_city(
    question: str,
    cities: list[str],
    aliases: dict | None,
) -> str | None:
    if not aliases or not isinstance(aliases, dict):
        return None
    lowered = question.casefold().replace("ё", "е")
    for alias, target in sorted(
        aliases.items(), key=lambda item: len(str(item[0])), reverse=True
    ):
        phrase = str(alias).casefold().replace("ё", "е").strip()
        if phrase and phrase in lowered:
            return canonical_city(str(target), cities) or str(target)
    return None


def _doctor_directory(
    knowledge_dir: Path | None,
    config: dict,
) -> tuple[str, Path, list["DirectoryRow"], list[str]] | None:
    """Pick the most complete available doctor list.

    Admins may keep several exports for a while. The old list is not allowed to
    shadow a newer, fuller one; once duplicates are removed the first remaining
    candidate still works.
    """
    names: list[str] = []
    raw_sources = config.get("sources")
    if isinstance(raw_sources, list):
        names.extend(str(item).strip() for item in raw_sources if str(item).strip())
    source = str(config.get("source") or "").strip()
    if source:
        names.append(source)
    names.extend(DEFAULT_CITY_SOURCES)
    names = list(dict.fromkeys(names))
    best: tuple[str, Path, list[DirectoryRow], list[str]] | None = None
    fallback: tuple[str, Path, list[DirectoryRow], list[str]] | None = None
    for name in names:
        path = _source_path(knowledge_dir, name)
        if path is None or not path.is_file():
            continue
        rows = load_rows(path)
        cities = load_cities(path)
        item = (path.name, path, rows, cities)
        if fallback is None:
            fallback = item
        if rows and (best is None or len(rows) > len(best[2])):
            best = item
    return best or fallback


def plan_turn(
    matched: list[MatchedSkill],
    *,
    question: str,
    memory: dict[str, str] | None = None,
    knowledge_dir: Path | None = None,
    context: str = "",
) -> TurnAdjustment:
    """Ask for a missing slot, or provide exact city/list/project context."""
    memory = memory or {}
    for item in matched:
        lookup = project_lookup(item.skill, question, knowledge_dir)
        if lookup is not None:
            return lookup
        list_result = list_lookup(item.skill, question, knowledge_dir)
        if list_result is not None:
            return list_result
        config = clarification_config(item.skill)
        if config is None:
            continue
        slot = str(config.get("slot") or "").strip().casefold()
        if slot != "city":
            continue
        intent = str(config.get("intent") or "").strip()
        if config.get("require_intent", True):
            haystack = f"{question}\n{context}"
            if intent:
                if not re.search(intent, haystack, re.IGNORECASE):
                    return TurnAdjustment()
            elif item.skill.name == "find-doctor" and not _DOCTOR_INTENT.search(haystack):
                return TurnAdjustment()
        directory = _doctor_directory(knowledge_dir, config)
        if directory is None:
            return TurnAdjustment()
        source_name, _path, rows, cities = directory
        city = _region_city(question, cities, config.get("region_aliases"))
        if city is None:
            city = extract_city(question, cities)
        if city is None:
            city = _region_city(
                str(memory.get("city") or ""),
                cities,
                config.get("region_aliases"),
            )
        if city is None:
            city = canonical_city(memory.get("city"), cities)
        if city is None and _looks_like_city_answer(question):
            # A bare «Сочи» after «в каком вы городе?» is a valid answer even if
            # the city is not present in the catalog.
            city = question.strip().strip(" .,!?;:")
        if not city:
            return TurnAdjustment(
                clarification=str(config.get("question") or DEFAULT_CITY_QUESTION),
                skill=item.skill.name,
            )
        if rows:
            skill_config = item.skill.config if isinstance(item.skill.config, dict) else {}
            limit = int(skill_config.get("max_rows") or config.get("max_rows") or DEFAULT_CITY_ROWS)
            chunks = city_context_chunks(
                rows,
                city,
                source=source_name,
                limit=limit,
            )
            if not chunks:
                return TurnAdjustment(
                    clarification=str(config.get("not_found") or DEFAULT_CITY_NOT_FOUND).format(city=city),
                    skill=item.skill.name,
                )
            return TurnAdjustment(
                city=city,
                skill=item.skill.name,
                exact_chunks=tuple(chunks),
                direct_answer=city_answer(
                    rows,
                    city,
                    limit=limit,
                ),
            )
        return TurnAdjustment(city=city, skill=item.skill.name)
    return TurnAdjustment()
