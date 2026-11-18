"""Slot policy: ask for a city instead of answering "no information"."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from packages.skills.models import MatchedSkill, Skill
from packages.skills.slots import (
    DEFAULT_CITY_QUESTION,
    canonical_city,
    city_stem,
    extract_city,
    load_cities,
    plan_turn,
)


def _doctor_file(tmp_path: Path) -> Path:
    path = tmp_path / "Список врачей.txt"
    path.write_text(
        "# source-file: Список врачей.xlsx\n"
        "# source-format: xlsx\n\n"
        "--- Sheet: Лист1 ---\n"
        "ФИО\tКонтакт для связи с пациентами\tГород\tЦентр\tСпециалиация\n"
        "Иванов Иван Иванович\t+7 900 000-00-00\tКраснодар\tКлиника А\tНевролог\n"
        "Петрова Анна Сергеевна\t+7 900 000-00-01\tМосква\tКлиника Б\tПаркинсонолог\n"
        "Сидорова Ольга Петровна\t+7 900 000-00-02\tУфа\tКлиника В\tНевролог\n"
        "Орлов Олег Олегович\t+7 900 000-00-03\tОрел\tКлиника Г\tНевролог\n",
        encoding="utf-8",
    )
    return path


def _find_doctor(config: dict | None = None) -> Skill:
    return Skill(
        id=4,
        tenant_id=1,
        name="find-doctor",
        title="Поиск врача",
        skill_type="domain",
        activation="match",
        description="Врачи",
        body="Правила",
        priority=85,
        sticky_turns=2,
        enabled=True,
        config=config or {},
    )


def test_city_stem_handles_common_russian_inflections() -> None:
    assert city_stem("Краснодаре") == city_stem("Краснодар")
    assert city_stem("Казани") == city_stem("Казань")
    assert city_stem("Москве") == city_stem("Москва")
    assert city_stem("Уфе") == city_stem("Уфа")


def test_load_cities_reads_the_city_column(tmp_path: Path) -> None:
    cities = load_cities(_doctor_file(tmp_path))
    assert set(cities) == {"Краснодар", "Москва", "Уфа", "Орел"}


def test_extract_city_from_question_and_answer(tmp_path: Path) -> None:
    cities = load_cities(_doctor_file(tmp_path))
    assert extract_city("Я живу в Краснодаре", cities) == "Краснодар"
    assert extract_city("в Уфе", cities) == "Уфа"
    assert extract_city("Краснодар", cities) == "Краснодар"
    assert extract_city("из Орла", cities) == "Орел"
    assert extract_city("Есть ли список врачей?", cities) is None


def test_canonical_city_maps_memory_value(tmp_path: Path) -> None:
    cities = load_cities(_doctor_file(tmp_path))
    assert canonical_city("Краснодаре", cities) == "Краснодар"
    assert canonical_city("Уфе", cities) == "Уфа"
    assert canonical_city("Орле", cities) == "Орел"
    assert canonical_city("неизвестный", cities) == "неизвестный"


def test_plan_turn_asks_for_a_city(tmp_path: Path) -> None:
    path = _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Подскажите контакты специалиста",
        memory={},
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification == DEFAULT_CITY_QUESTION
    assert adjustment.city is None


def test_plan_turn_uses_memory_city_and_canonical_spelling(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Есть ли список врачей?",
        memory={"city": "Краснодаре"},
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification is None
    assert adjustment.city == "Краснодар"


def test_plan_turn_uses_city_from_the_question(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="А есть врачи в Уфе?",
        memory={},
        knowledge_dir=tmp_path,
    )
    assert adjustment.city == "Уфа"


def test_default_clarify_applies_to_older_find_doctor_rows(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(config={}), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="К кому обратиться с диагнозом БП?",
        memory={},
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification == DEFAULT_CITY_QUESTION


def test_non_doctor_skill_does_not_ask(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    skill = replace(_find_doctor(config={}), name="medications")
    matched = [MatchedSkill(skill=skill, score=0.9)]
    assert plan_turn(matched, question="Подскажите контакты", memory={}, knowledge_dir=tmp_path).clarification is None


def test_city_answer_lists_every_row_and_no_other_city(tmp_path: Path) -> None:
    path = _doctor_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "Смирнов Петр Петрович\t+7 900 000-00-04\tКраснодар\tКлиника Д\tНевролог\n",
        encoding="utf-8",
    )
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Есть ли список врачей? Краснодар",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert "Иванов Иван Иванович" in adjustment.direct_answer
    assert "Смирнов Петр Петрович" in adjustment.direct_answer
    assert "Петрова Анна Сергеевна" not in adjustment.direct_answer


def test_city_intent_guard_does_not_volunteer_doctors(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Я живу в Краснодаре",
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification is None
    assert adjustment.direct_answer is None
    assert adjustment.city is None


def test_city_followup_uses_context_intent(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Краснодар",
        context="Есть ли список врачей?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert "Иванов Иван Иванович" in adjustment.direct_answer


def test_unknown_city_gets_a_polite_followup(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="А есть врачи в Сочи?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification
    assert "Сочи" in adjustment.clarification
    assert adjustment.direct_answer is None


def test_bare_unknown_city_is_treated_as_an_answer(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Сочи",
        context="Есть ли список врачей?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification
    assert "Сочи" in adjustment.clarification


def test_thanks_does_not_become_a_city_name(tmp_path: Path) -> None:
    _doctor_file(tmp_path)
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Спасибо",
        context="Есть ли список врачей?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.clarification == DEFAULT_CITY_QUESTION


def _navigator_file(tmp_path: Path) -> Path:
    path = tmp_path / "Навигатор по проектам фонда.txt"
    path.write_text(
        "1.1. Игры Паркинсона\n"
        "Соревнования и тренировки.\n\n"
        "1.2. Команда Мечты\n"
        "Статус: готовится отдельный проект на сайте.\n"
        "Как вступить:\n"
        "Связаться с капитаном — Иван Иванов, +7 900 111-22-33\n"
        "Заполнить анкету: https://forms.example/team\n"
        "Взнос: 400 ₽.\n"
        "Оплата — перевод: https://tbank.example/pay\n"
        "Как отвечать: набор завершён.\n\n"
        "1.7. Творческий онлайн-клуб «ДО-ФА-МИ»\n"
        "Творческий клуб: музыка, каллиграфия, встречи.\n"
        "Как вступить: написать в ВК.\n",
        encoding="utf-8",
    )
    return path


def test_project_lookup_uses_how_to_join_section(tmp_path: Path) -> None:
    _navigator_file(tmp_path)
    skill = _find_doctor(
        config={
            "lookup": {
                "source": "Навигатор по проектам фонда.txt",
                "items": [{"terms": ["команда мечты"], "heading": "Команда Мечты"}],
            }
        }
    )
    skill = replace(skill, name="fund-projects")
    adjustment = plan_turn(
        [MatchedSkill(skill=skill, score=0.9)],
        question="Как попасть в Команду Мечты?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert "Иван Иванов" in adjustment.direct_answer
    assert "forms.example/team" in adjustment.direct_answer
    assert "недоступ" not in adjustment.direct_answer.casefold()
    assert "Как отвечать" not in adjustment.direct_answer
    assert "tbank.example" not in adjustment.direct_answer
    assert adjustment.exact_chunks[0].source == "Навигатор по проектам фонда.txt"


def test_project_lookup_does_not_mix_club_with_other_projects(tmp_path: Path) -> None:
    _navigator_file(tmp_path)
    skill = _find_doctor(
        config={
            "lookup": {
                "source": "Навигатор по проектам фонда.txt",
                "items": [{"terms": ["до-фа-ми"], "heading": "ДО-ФА-МИ"}],
            }
        }
    )
    skill = replace(skill, name="fund-projects")
    adjustment = plan_turn(
        [MatchedSkill(skill=skill, score=0.9)],
        question="Что такое клуб «До-Фа-Ми»?",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert "Творческий клуб" in adjustment.direct_answer
    assert "Иван Иванов" not in adjustment.direct_answer


def test_city_answer_deduplicates_same_doctor(tmp_path: Path) -> None:
    path = _doctor_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "Иванов Иван Иванович\t+7 900 000-00-00\tКраснодар\tКлиника А\tНевролог\n",
        encoding="utf-8",
    )
    matched = [MatchedSkill(skill=_find_doctor(), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Есть ли список врачей? Краснодар",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert adjustment.direct_answer.count("Иванов Иван Иванович") == 1


def test_city_answer_respects_skill_max_rows(tmp_path: Path) -> None:
    path = _doctor_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "Смирнов Петр Петрович\t+7 900 000-00-04\tКраснодар\tКлиника Д\tНевролог\n"
        + "Сидоров Иван Петрович\t+7 900 000-00-05\tКраснодар\tКлиника Е\tНевролог\n",
        encoding="utf-8",
    )
    matched = [MatchedSkill(skill=_find_doctor(config={"max_rows": 2}), score=0.9)]
    adjustment = plan_turn(
        matched,
        question="Есть ли список врачей? Краснодар",
        knowledge_dir=tmp_path,
    )
    assert adjustment.direct_answer
    assert adjustment.direct_answer.count("- **") == 2
    assert "показаны первые 2" in adjustment.direct_answer
