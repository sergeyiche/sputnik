"""Semantic domain-skill router on top of the shared embeddings model."""

from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings

from packages.skills.models import MatchedSkill, Skill

logger = logging.getLogger(__name__)

# Strong, deterministic intent words. They let an explicit doctor request work
# even when its embedding score is just below the calibrated threshold.
FIND_DOCTOR_MATCH_ANY = (
    "врач",
    "доктор",
    "невролог",
    "паркинсонолог",
    "клиник",
    "список врачей",
    "контакты врача",
    "к кому из врачей",
    "где найти врача",
)
FIND_DOCTOR_AVOID_ANY = (
    "льгот",
    "юрид",
    "инвалид",
    "мсэ",
    "пособ",
    "выплат",
    "правов",
    "о фонде",
    "что говорят врачи",
    "мнение врачей",
    "врачи советуют",
    "врачи рекомендуют",
    "по словам врачей",
    "врачи при",
    "не нужен врач",
    "не нужен специалист",
)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a)) or 1.0


def cosine(a: list[float], b: list[float]) -> float:
    return _dot(a, b) / (_norm(a) * _norm(b))


@dataclass
class StickySkillState:
    """In-process sticky domain skills per chat session."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _by_session: dict[str, dict[str, int]] = field(default_factory=dict)

    def remaining(self, session_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._by_session.get(session_id, {}))

    def commit(self, session_id: str, matched: list[MatchedSkill]) -> None:
        """Refresh sticky counters for newly matched skills; decrement others."""
        with self._lock:
            current = dict(self._by_session.get(session_id, {}))
            hit_names = {item.skill.name for item in matched if not item.sticky}
            for item in matched:
                if item.sticky:
                    continue
                turns = max(0, int(item.skill.sticky_turns))
                if turns:
                    current[item.skill.name] = turns
            for name in list(current):
                if name in hit_names:
                    continue
                current[name] -= 1
                if current[name] <= 0:
                    del current[name]
            if current:
                self._by_session[session_id] = current
            else:
                self._by_session.pop(session_id, None)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


class SkillRouter:
    """Match a user question to enabled domain skills via example embeddings."""

    def __init__(
        self,
        embeddings: Embeddings,
        *,
        threshold: float | None = None,
        max_active: int | None = None,
        margin: float | None = None,
    ) -> None:
        self._embeddings = embeddings
        # e5-family embeddings have a high cosine baseline (~0.75–0.85 even for
        # unrelated text), so the GigaChat-oriented 0.42 threshold matches every
        # skill on every question. Keep a provider-specific default.
        provider = os.getenv("EMBEDDINGS_PROVIDER", "gigachat").lower()
        default_threshold = 0.86 if provider == "local" else 0.42
        if threshold is not None:
            self._threshold = threshold
        else:
            self._threshold = float(os.getenv("SKILLS_SCORE_THRESHOLD", str(default_threshold)))
            # A stale 0.42 from the GigaChat-era config activates all skills on
            # e5 embeddings. Clamp it instead of silently routing everything.
            if provider == "local" and self._threshold < 0.7:
                logger.warning(
                    "SKILLS_SCORE_THRESHOLD=%.2f is below the e5 baseline; using 0.86",
                    self._threshold,
                )
                self._threshold = 0.86
        self._margin = margin if margin is not None else float(
            os.getenv("SKILLS_SCORE_MARGIN", "0.04")
        )
        self._max_active = max_active if max_active is not None else int(
            os.getenv("SKILLS_MAX_ACTIVE", "2")
        )
        self._lock = threading.Lock()
        # tenant_id -> skill_id -> list[vector]
        self._cache: dict[int, dict[int, list[list[float]]]] = {}
        self._cache_rev: dict[int, int] = {}

    def invalidate(self, tenant_id: int) -> None:
        with self._lock:
            self._cache.pop(tenant_id, None)
            self._cache_rev.pop(tenant_id, None)

    def ensure_cached(self, tenant_id: int, skills: list[Skill], *, revision: int) -> None:
        with self._lock:
            if self._cache_rev.get(tenant_id) == revision and tenant_id in self._cache:
                return
        vectors: dict[int, list[list[float]]] = {}
        for skill in skills:
            if skill.skill_type != "domain" or not skill.enabled:
                continue
            texts = [ex.text for ex in skill.examples]
            if not texts:
                texts = [skill.description]
            vectors[skill.id] = self._embeddings.embed_documents(texts)
        with self._lock:
            self._cache[tenant_id] = vectors
            self._cache_rev[tenant_id] = revision

    def match(
        self,
        tenant_id: int,
        skills: list[Skill],
        question: str,
        *,
        sticky_names: dict[str, int] | None = None,
        revision: int = 0,
    ) -> list[MatchedSkill]:
        question = question.strip()
        if not question:
            return []

        domain = [s for s in skills if s.skill_type == "domain" and s.enabled]
        if not domain:
            return []

        self.ensure_cached(tenant_id, domain, revision=revision)
        with self._lock:
            cached = self._cache.get(tenant_id, {})

        query_vec = self._embeddings.embed_query(question)
        lowered = question.casefold()
        scored: list[MatchedSkill] = []
        for skill in domain:
            if _avoid_any(skill, lowered):
                continue
            example_vecs = cached.get(skill.id) or []
            if not example_vecs:
                continue
            best = max(cosine(query_vec, vec) for vec in example_vecs)
            if best >= self._threshold:
                scored.append(MatchedSkill(skill=skill, score=best, sticky=False))

        # Explicit intent beats a marginal embedding score. Without this,
        # «Какие врачи есть в Москве?» (≈0.850) falls just below the e5 threshold.
        matched_ids = {item.skill.id for item in scored}
        explicit_ids: set[int] = set()
        for skill in domain:
            if _avoid_any(skill, lowered):
                continue
            hints = _match_any(skill)
            if not hints or not any(hint.casefold() in lowered for hint in hints):
                continue
            explicit_ids.add(skill.id)
            if skill.id not in matched_ids:
                scored.append(MatchedSkill(skill=skill, score=self._threshold, sticky=False))

        if not scored:
            return []
        # A deterministic intent is stronger than a near-threshold semantic
        # neighbour. Without this, «Республика Адыгея» can pull in urinary
        # instructions just because e5 scores everything around 0.86.
        if explicit_ids:
            scored = [item for item in scored if item.skill.id in explicit_ids]
        best_score = max(item.score for item in scored)
        # Keep only skills close to the best match: a single question should not
        # activate every topic that happens to clear the threshold.
        scored = [item for item in scored if best_score - item.score <= self._margin]
        scored.sort(key=lambda item: (item.skill.priority, item.score), reverse=True)
        selected = scored[: self._max_active]
        selected_names = {item.skill.name for item in selected}

        sticky_names = sticky_names or {}
        by_name = {s.name: s for s in domain}
        for name, _remaining in sticky_names.items():
            if name in selected_names:
                continue
            skill = by_name.get(name)
            if skill is None:
                continue
            selected.append(MatchedSkill(skill=skill, score=0.0, sticky=True))

        selected.sort(key=lambda item: (item.skill.priority, item.score), reverse=True)
        return selected[: self._max_active]


def _match_any(skill: Skill) -> tuple[str, ...]:
    if isinstance(skill.config, dict):
        raw = skill.config.get("match_any")
        if isinstance(raw, list):
            return tuple(str(item).strip() for item in raw if str(item).strip())
    if skill.skill_type == "domain" and skill.name == "find-doctor":
        return FIND_DOCTOR_MATCH_ANY
    return ()


def _avoid_any(skill: Skill, question: str) -> bool:
    if isinstance(skill.config, dict) and "avoid_any" in skill.config:
        raw = skill.config.get("avoid_any")
        terms = tuple(str(item).strip() for item in raw or [] if str(item).strip())
    elif skill.skill_type == "domain" and skill.name == "find-doctor":
        terms = FIND_DOCTOR_AVOID_ANY
    else:
        terms = ()
    return any(term.casefold() in question for term in terms)


def enrich_search_query(base: str, matched: list[MatchedSkill]) -> str:
    hints: list[str] = []
    for item in matched:
        hints.extend(item.skill.query_hints[:3])
    if not hints:
        return base
    # Keep query compact for the embedder
    unique = list(dict.fromkeys(hints))[:6]
    return f"{base.strip()}\n{' '.join(unique)}"


def boost_documents(matched: list[MatchedSkill]) -> list[str]:
    names: list[str] = []
    for item in matched:
        names.extend(item.skill.retrieval_boost)
    return list(dict.fromkeys(names))


def merge_skill_instructions(base_prompt: str, matched: list[MatchedSkill]) -> str:
    active = [item for item in matched if item.skill.body.strip()]
    if not active:
        return base_prompt
    # Context policies go last: they are cross-cutting formatting/dialogue rules
    # and must not be overridden by a topic skill's short format section.
    domain = [item for item in active if item.skill.skill_type != "context"]
    context = [item for item in active if item.skill.skill_type == "context"]
    active = [*domain, *context]
    blocks = [base_prompt.rstrip(), "", "## Активные навыки", ""]
    for item in active:
        skill = item.skill
        blocks.append(f"### {skill.title}")
        blocks.append(skill.body.strip())
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def followup_suggestions(
    matched: list[MatchedSkill],
    *,
    asked: str,
    limit: int | None = None,
) -> list[str]:
    """Chip texts after an answer: config.suggestions first, then unused examples."""
    if limit is None:
        limit = int(os.getenv("SKILLS_SUGGESTION_COUNT", "3") or "3")
    limit = max(0, min(limit, 6))
    if not matched or limit == 0:
        return []

    asked_norm = " ".join(asked.strip().lower().split())
    seen: set[str] = {asked_norm} if asked_norm else set()
    out: list[str] = []

    for item in matched:
        pool: list[str] = []
        cfg = item.skill.config if isinstance(item.skill.config, dict) else {}
        raw = cfg.get("suggestions")
        if isinstance(raw, list):
            pool.extend(str(x).strip() for x in raw if str(x).strip())
        pool.extend(ex.text.strip() for ex in item.skill.examples if ex.text.strip())
        for text in pool:
            key = " ".join(text.lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return out
    return out
