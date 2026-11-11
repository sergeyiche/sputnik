"""Apply enabled skills to a chat turn (context + domain route → prompt/search/boost)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from packages.platform.store import PlatformStore
from packages.skills.models import MatchedSkill, Skill
from packages.skills.router import (
    SkillRouter,
    StickySkillState,
    boost_documents,
    enrich_search_query,
    followup_suggestions,
    merge_skill_instructions,
)


@dataclass(frozen=True)
class SkillTurn:
    matched: list[MatchedSkill]
    system_prompt: str | None
    search_query: str
    boost_sources: list[str]
    suggestions: tuple[str, ...] = ()

    def payload(self) -> list[dict]:
        return [
            {
                "name": item.skill.name,
                "title": item.skill.title,
                "type": item.skill.type,
                "score": round(item.score, 4),
                "sticky": item.sticky,
            }
            for item in self.matched
        ]


def skills_enabled() -> bool:
    return os.getenv("SKILLS_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def select_context_skills(skills: list[Skill]) -> list[MatchedSkill]:
    """Context skills use their own activation channel (not domain score)."""
    active: list[MatchedSkill] = []
    for skill in skills:
        if skill.skill_type != "context" or not skill.enabled:
            continue
        if skill.activation == "always":
            active.append(MatchedSkill(skill=skill, score=1.0, sticky=False))
    active.sort(key=lambda item: item.skill.priority, reverse=True)
    return active


def is_attention_enabled(store: PlatformStore, tenant_id: int | None) -> bool:
    """Topic rewrite is on unless the tenant has an explicit disabled ``attention`` skill.

    Tenants without the skill keep legacy always-on follow-up behaviour.
    """
    if not skills_enabled() or tenant_id is None:
        return True
    skills = store.list_skills(tenant_id, enabled_only=False)
    attention = next(
        (s for s in skills if s.skill_type == "context" and s.name == "attention"),
        None,
    )
    if attention is None:
        return True
    return bool(attention.enabled)


def get_context_skill(store: PlatformStore, tenant_id: int | None, name: str) -> Skill | None:
    if not skills_enabled() or tenant_id is None:
        return None
    for skill in store.list_skills(tenant_id, enabled_only=True):
        if skill.skill_type == "context" and skill.name == name:
            return skill
    return None


def is_memory_enabled(store: PlatformStore, tenant_id: int | None) -> bool:
    return get_context_skill(store, tenant_id, "dialogue-memory") is not None


def memory_config(skill: Skill | None) -> dict:
    if skill is None or not isinstance(skill.config, dict):
        return {"max_facts": 12, "use_llm_extract": True}
    return {
        "max_facts": int(skill.config.get("max_facts") or 12),
        "use_llm_extract": bool(skill.config.get("use_llm_extract", True)),
    }


def apply_skills(
    *,
    store: PlatformStore,
    sticky: StickySkillState,
    tenant_id: int | None,
    session_id: str,
    question: str,
    search_query: str,
    base_prompt: str,
    reset_sticky: bool = False,
    router: SkillRouter | None = None,
    router_factory: Callable[[], SkillRouter] | None = None,
) -> SkillTurn:
    empty = SkillTurn(
        matched=[],
        system_prompt=None,
        search_query=search_query,
        boost_sources=[],
        suggestions=[],
    )
    if not skills_enabled() or tenant_id is None:
        return empty

    skills = store.list_skills(tenant_id, enabled_only=True)
    if not skills:
        return empty

    if reset_sticky:
        sticky.clear(session_id)

    context = select_context_skills(skills)

    if router is None:
        if router_factory is None:
            raise RuntimeError("router or router_factory is required when skills exist")
        router = router_factory()

    revision = store.skills_revision(tenant_id)
    # Domain route scores the resolved standalone question, not the raw short follow-up.
    route_text = (search_query or question).strip() or question.strip()
    matched_domain = router.match(
        tenant_id,
        skills,
        route_text,
        sticky_names=sticky.remaining(session_id),
        revision=revision,
    )
    sticky.commit(session_id, matched_domain)

    matched = [*context, *matched_domain]
    return SkillTurn(
        matched=matched,
        system_prompt=merge_skill_instructions(base_prompt, matched) if matched else None,
        search_query=enrich_search_query(search_query, matched_domain),
        boost_sources=boost_documents(matched_domain),
        suggestions=tuple(followup_suggestions(matched_domain, asked=question)),
    )
