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


def test_seed_upgrades_old_find_doctor_with_city_clarify(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    fund = store.register(slug="parkinson", name="Фонд", email="f2@parkinson.org", password="secret123")
    doctor = next(skill for skill in store.list_skills(fund.id) if skill.name == "find-doctor")
    assert doctor.config["clarify"]["slot"] == "city"

    store.update_skill(
        fund.id,
        doctor.id,
        {
            "body": "Старые правила. Если списка нет в контексте — скажи об этом.",
            "config": {"suggestions": ["Есть ли список врачей?"]},
        },
    )
    store.seed_parkinson_skills(fund.id)
    upgraded = store.get_skill(fund.id, doctor.id)
    assert "Спроси, из какого он города" in upgraded.body
    assert upgraded.config["clarify"]["source"] == "Список врачей паркинсонологов.txt"
    assert upgraded.config["match_any"]
    assert upgraded.config["avoid_any"]
    assert upgraded.config["max_rows"] == 10
    assert upgraded.config["suggestions"] == ["Есть ли список врачей?"]


def test_router_margin_and_max_active() -> None:
    from packages.skills.router import SkillRouter

    class Scored:
        def __init__(self) -> None:
            self.vectors: dict[str, list[float]] = {}

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [self.vectors.setdefault(text, [1.0, 0.0]) for text in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    emb = Scored()
    first = _skill(id=1, examples=(SkillExample(id=1, text="a"),))
    second = _skill(id=2, name="exercises", title="Упражнения", priority=50, examples=(SkillExample(id=2, text="b"),))
    third = _skill(id=3, name="fund-projects", title="Фонд", priority=40, examples=(SkillExample(id=3, text="c"),))
    # All three clear the threshold; the margin should keep only the best.
    emb.vectors.update({"a": [1.0, 0.0], "b": [0.8, 0.6], "c": [0.7, 0.7]})
    router = SkillRouter(emb, threshold=0.5, max_active=2, margin=0.05)
    matched = router.match(1, [first, second, third], "a", revision=1)
    assert [item.skill.name for item in matched] == ["medications"]
    assert len(matched) <= 2


def test_router_rejects_the_e5_baseline() -> None:
    from packages.skills.router import SkillRouter

    class Baseline:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.82, 0.57] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    skill = _skill(examples=(SkillExample(id=1, text="a"),))
    router = SkillRouter(Baseline(), threshold=0.86, max_active=2)
    assert router.match(1, [skill], "привет", revision=1) == []


def test_explicit_doctor_words_activate_find_doctor_below_threshold() -> None:
    from packages.skills.router import SkillRouter

    class BelowThreshold:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.85, 0.5268]

    doctor = _skill(
        name="find-doctor",
        title="Поиск врача",
        config={},
        examples=(SkillExample(id=1, text="Где найти невролога?"),),
    )
    router = SkillRouter(BelowThreshold(), threshold=0.86, max_active=2)
    matched = router.match(1, [doctor], "Какие врачи есть в Москве?", revision=1)
    assert [item.skill.name for item in matched] == ["find-doctor"]
    assert router.match(1, [doctor], "Спасибо", revision=1) == []


def test_router_avoid_any_blocks_doctor_on_legal_question() -> None:
    from packages.skills.router import SkillRouter

    class FakeEmb:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    doctor = _skill(
        name="find-doctor",
        title="Поиск врача",
        config={"avoid_any": ["льгот", "юрид", "инвалид"]},
        examples=(SkillExample(id=1, text="Где найти врача?"),),
    )
    router = SkillRouter(FakeEmb(), threshold=0.5, max_active=2)
    assert router.match(1, [doctor], "К какому специалисту обратиться по льготам?", revision=1) == []
    assert router.match(1, [doctor], "Где найти врача?", revision=1)[0].skill.name == "find-doctor"


def test_fund_projects_seed_has_no_donation_chip_and_no_donate_doc(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    fund = store.register(slug="parkinson", name="Фонд", email="f3@parkinson.org", password="secret123")
    projects = next(skill for skill in store.list_skills(fund.id) if skill.name == "fund-projects")
    assert "Как поддержать фонд пожертвованием?" not in projects.config["suggestions"]
    assert "О фонде.txt" not in projects.retrieval_boost
    assert "Не выводи «проект недоступен»" in projects.body


def test_legal_social_seed_exists(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    fund = store.register(slug="parkinson", name="Фонд", email="f4@parkinson.org", password="secret123")
    legal = next(skill for skill in store.list_skills(fund.id) if skill.name == "legal-social")
    assert legal.skill_type == "domain" and legal.enabled
    assert "правовая поддержка БП 2026.txt" in legal.retrieval_boost
    find_doctor = next(skill for skill in store.list_skills(fund.id) if skill.name == "find-doctor")
    assert find_doctor.config["max_rows"] == 10
    assert "специалист" not in find_doctor.config["match_any"]
    assert "льгот" in find_doctor.config["avoid_any"]


def test_seed_removes_old_fund_donation_chip_and_boost(tmp_path: Path) -> None:
    store = PlatformStore(tmp_path / "platform.db")
    fund = store.register(slug="parkinson", name="Фонд", email="f5@parkinson.org", password="secret123")
    projects = next(skill for skill in store.list_skills(fund.id) if skill.name == "fund-projects")
    store.update_skill(
        fund.id,
        projects.id,
        {
            "body": "Старые правила. Донаты — только если человек сам спросил.",
            "retrieval_boost": ["Навигатор по проектам фонда.txt", "О фонде.txt"],
            "config": {"suggestions": ["Как поддержать фонд пожертвованием?"]},
        },
    )
    store.seed_parkinson_skills(fund.id)
    upgraded = store.get_skill(fund.id, projects.id)
    assert "О фонде.txt" not in upgraded.retrieval_boost
    assert "Как поддержать фонд пожертвованием?" not in upgraded.config["suggestions"]
    assert "Не выводи «проект недоступен»" in upgraded.body
