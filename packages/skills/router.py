"""Semantic domain-skill router on top of the shared embeddings model."""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings

from packages.skills.models import MatchedSkill, Skill


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
    ) -> None:
        self._embeddings = embeddings
        self._threshold = threshold if threshold is not None else float(
            os.getenv("SKILLS_SCORE_THRESHOLD", "0.42")
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
        scored: list[MatchedSkill] = []
        for skill in domain:
            example_vecs = cached.get(skill.id) or []
            if not example_vecs:
                continue
            best = max(cosine(query_vec, vec) for vec in example_vecs)
            if best >= self._threshold:
                scored.append(MatchedSkill(skill=skill, score=best, sticky=False))

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
        return selected[: max(self._max_active, len(selected_names))]


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
