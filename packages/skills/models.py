"""Skill records shared by the platform store and the router."""

from __future__ import annotations

from dataclasses import dataclass, field


SKILL_TYPES = frozenset({"domain", "context", "workflow"})
ACTIVATIONS = frozenset({"match", "always", "intent", "manual"})
NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


@dataclass(frozen=True)
class SkillExample:
    id: int
    text: str


@dataclass(frozen=True)
class Skill:
    id: int
    tenant_id: int
    name: str
    title: str
    skill_type: str
    activation: str
    description: str
    body: str
    priority: int
    sticky_turns: int
    enabled: bool
    retrieval_boost: list[str] = field(default_factory=list)
    query_hints: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    examples: tuple[SkillExample, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    @property
    def type(self) -> str:
        """API-facing alias for ``skill_type``."""
        return self.skill_type


@dataclass(frozen=True)
class MatchedSkill:
    skill: Skill
    score: float
    sticky: bool = False
