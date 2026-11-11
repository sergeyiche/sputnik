"""Skills store, router helpers and seed."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.platform.store import PlatformError, PlatformStore
from packages.skills.router import (
    StickySkillState,
    boost_documents,
    cosine,
    enrich_search_query,
    followup_suggestions,
    merge_skill_instructions,
)
from packages.skills.models import MatchedSkill, Skill, SkillExample
from packages.skills.runtime import is_attention_enabled, select_context_skills
from packages.skills.seeds import PARKINSON_SKILL_SEEDS


def _skill(**overrides) -> Skill:
    base = dict(
        id=1,
        tenant_id=1,
        name="medications",
        title="Лекарства",
        skill_type="domain",
        activation="match",
        description="desc",
        body="## Правила\n- только справочно",
        priority=80,
        sticky_turns=2,
        enabled=True,
        retrieval_boost=["a.txt"],
        query_hints=["леводопа"],
        examples=(),
    )
    base.update(overrides)
    return Skill(**base)


def test_cosine_identical_vectors() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_merge_and_enrich_helpers() -> None:
    matched = [MatchedSkill(skill=_skill(), score=0.9)]
    prompt = merge_skill_instructions("База.", matched)
    assert "Активные навыки" in prompt and "Лекарства" in prompt
    assert "леводопа" in enrich_search_query("вопрос", matched)
    assert boost_documents(matched) == ["a.txt"]


def test_followup_suggestions_prefer_config_then_examples() -> None:
    skill = _skill(
        config={"suggestions": ["А дозировку?", "А побочные эффекты?"]},
        examples=(
            SkillExample(id=1, text="Какие лекарства применяют?"),
            SkillExample(id=2, text="А дозировку?"),
        ),
    )
    matched = [MatchedSkill(skill=skill, score=0.9)]
    chips = followup_suggestions(matched, asked="Какие лекарства применяют?", limit=3)
    assert chips[0] == "А дозировку?"
    assert "А побочные эффекты?" in chips
    assert "Какие лекарства применяют?" not in chips


def test_sticky_state_decrements() -> None:
    sticky = StickySkillState()
    matched = [MatchedSkill(skill=_skill(sticky_turns=2), score=0.8)]
    sticky.commit("s1", matched)
    assert sticky.remaining("s1") == {"medications": 2}
    sticky.commit("s1", [])
    assert sticky.remaining("s1") == {"medications": 1}
    sticky.commit("s1", [])
    assert sticky.remaining("s1") == {}


def test_sticky_clear_resets_session() -> None:
    sticky = StickySkillState()
    sticky.commit("s1", [MatchedSkill(skill=_skill(sticky_turns=2), score=0.8)])
    sticky.clear("s1")
    assert sticky.remaining("s1") == {}


def test_select_context_skills_always() -> None:
    attention = _skill(
        id=9,
        name="attention",
        title="Внимание",
        skill_type="context",
        activation="always",
        priority=100,
        sticky_turns=0,
        retrieval_boost=[],
        query_hints=[],
    )
    domain = _skill(id=1)
    picked = select_context_skills([attention, domain])
    assert len(picked) == 1 and picked[0].skill.name == "attention"


def test_skill_crud_and_parkinson_seed(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    acme = store.register(slug="acme", name="A", email="a@acme.org", password="secret123")
    assert store.list_skills(acme.id) == []

    created = store.create_skill(
        acme.id,
        {
            "name": "find-doctor",
            "title": "Врачи",
            "description": "Список врачей и неврологов. Use when asking for a doctor.",
            "body": "Перечисляй только из контекста.",
            "examples": ["Где найти невролога?", "Список врачей"],
            "retrieval_boost": ["Список врачей.txt"],
        },
    )
    assert created.name == "find-doctor"
    assert len(created.examples) == 2

    updated = store.update_skill(acme.id, created.id, {"enabled": False, "priority": 90})
    assert updated.enabled is False and updated.priority == 90

    with pytest.raises(PlatformError):
        store.create_skill(
            acme.id,
            {
                "name": "find-doctor",
                "title": "Dup",
                "description": "x" * 20,
                "body": "y" * 20,
            },
        )

    store.delete_skill(acme.id, created.id)
    assert store.list_skills(acme.id) == []

    fund = store.register(slug="parkinson", name="Фонд", email="f@parkinson.org", password="secret123")
    assert {s.name for s in store.list_skills(fund.id)} == {s["name"] for s in PARKINSON_SKILL_SEEDS}
    assert store.seed_parkinson_skills(fund.id) == []
    assert is_attention_enabled(store, fund.id) is True
    store.update_skill(fund.id, next(s.id for s in store.list_skills(fund.id) if s.name == "attention"), {"enabled": False})
    assert is_attention_enabled(store, fund.id) is False


def test_router_match_with_fake_embeddings() -> None:
    from packages.skills.router import SkillRouter

    class FakeEmb:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] if "лекар" in t.lower() or "леводоп" in t.lower() else [0.0, 1.0] for t in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0] if "лекар" in text.lower() or "леводоп" in text.lower() else [0.0, 1.0]

    skill = _skill(
        id=7,
        examples=(),
    )
    # Rebuild with examples via store-like structure
    from packages.skills.models import SkillExample

    skill = _skill(
        id=7,
        examples=(
            SkillExample(id=1, text="Какие лекарства применяют?"),
            SkillExample(id=2, text="Побочные эффекты леводопы"),
        ),
    )
    other = _skill(
        id=8,
        name="exercises",
        title="Упражнения",
        priority=50,
        examples=(SkillExample(id=3, text="Какая гимнастика полезна?"),),
        retrieval_boost=[],
        query_hints=[],
    )
    router = SkillRouter(FakeEmb(), threshold=0.5, max_active=2)
    matched = router.match(1, [skill, other], "Расскажите про лекарства леводопу", revision=1)
    assert matched and matched[0].skill.name == "medications"
