"""Normalize ``[n]`` source citations produced by the LLM."""

from __future__ import annotations

import re

_NUMBER_LIST = r"\d+(?:\s*[,;]\s*\d+)*"
CITATION_GROUP = re.compile(rf"\[({_NUMBER_LIST})\](?!\()")
SOURCE_LABEL = re.compile(
    rf"\(?\s*(?:Источник|Источники|Source|Sources)\s*:\s*"
    rf"((?:\[{_NUMBER_LIST}\][\s,;и]*)+)\)?",
    re.IGNORECASE,
)
MARKER_AFTER_PUNCT = re.compile(r"\s*([.!?])\s*((?:\[\d+\])+)(?=\s|$)")
SPACE_BEFORE_MARKER = re.compile(r"[ \t]+((?:\[\d+\])+)(?!\()")


def normalize_citations(text: str, valid_numbers: set[int]) -> tuple[str, list[int]]:
    """Return ``(text, cited)`` with markers as ``[1][3]`` and unknown numbers removed.

    ``cited`` keeps the order of first appearance.
    """
    cited: list[int] = []

    def unlabel(match: re.Match[str]) -> str:
        return " " + match.group(1).strip(" ,;и\t")

    def rewrite(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.split(r"\s*[,;]\s*", match.group(1))]
        kept = [n for n in dict.fromkeys(numbers) if n in valid_numbers]
        for number in kept:
            if number not in cited:
                cited.append(number)
        return "".join(f"[{n}]" for n in kept)

    result = SOURCE_LABEL.sub(unlabel, text)
    result = CITATION_GROUP.sub(rewrite, result)
    result = MARKER_AFTER_PUNCT.sub(r"\2\1", result)
    result = SPACE_BEFORE_MARKER.sub(r"\1", result)
    result = re.sub(r"[ \t]+([.,;:!?])", r"\1", result)
    return result, cited


def strip_citations(text: str) -> str:
    """Remove all ``[n]`` markers (numbering is per-turn, so history must not keep them)."""
    without = CITATION_GROUP.sub("", SOURCE_LABEL.sub("", text))
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", without)
