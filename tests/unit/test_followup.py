"""Follow-up questions stay on the dialogue topic instead of the previous short turn."""

from __future__ import annotations

from packages.dialogue.followup import fallback_standalone, is_followup, resolve_turn
from packages.rag.pipeline import RAGPipeline


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content


class _ScriptedLLM:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def invoke(self, messages: list) -> _Reply:
        self.prompts.append(messages[-1].content)
        return _Reply(self._answers.pop(0))


def test_short_followup_chain_keeps_the_original_topic_without_llm() -> None:
    history = [
        {"role": "user", "content": "Расскажи про леводопу"},
        {"role": "assistant", "content": "Леводопа — основной препарат."},
    ]
    first, topic = resolve_turn(None, topic="Расскажи про леводопу", history=history, question="а побочные?")
    assert first == "Расскажи про леводопу\nа побочные?"
    assert topic == "Расскажи про леводопу"

    history = [
        *history,
        {"role": "user", "content": "а побочные?"},
        {"role": "assistant", "content": "Тошнота и дискинезии."},
    ]
    second, topic_after = resolve_turn(None, topic=topic, history=history, question="а как их снизить?")
    assert second == "Расскажи про леводопу\nа как их снизить?"
    assert "а побочные?" not in second
    assert topic_after == "Расскажи про леводопу"


def test_llm_failure_uses_the_saved_topic() -> None:
    class _Broken:
        def invoke(self, messages: list) -> _Reply:
            raise RuntimeError("model down")

    history = [
        {"role": "user", "content": "Расскажи про леводопу"},
        {"role": "assistant", "content": "Кратко."},
    ]
    query, topic = resolve_turn(
        _Broken(),
        topic="Расскажи про леводопу",
        history=history,
        question="а доза?",
    )
    assert query == fallback_standalone("Расскажи про леводопу", "а доза?")
    assert topic == "Расскажи про леводопу"


def test_rewrite_replaces_the_topic_with_a_standalone_question() -> None:
    llm = _ScriptedLLM(["побочные эффекты леводопы", "как снизить побочные эффекты леводопы"])
    history = [
        {"role": "user", "content": "Расскажи про леводопу"},
        {"role": "assistant", "content": "Леводопа — основной препарат."},
    ]
    first, topic = resolve_turn(llm, topic="Расскажи про леводопу", history=history, question="а побочные?")
    assert first == "побочные эффекты леводопы"
    assert "Расскажи про леводопу" in llm.prompts[0]

    history = [
        *history,
        {"role": "user", "content": "а побочные?"},
        {"role": "assistant", "content": "Тошнота."},
    ]
    second, topic_after = resolve_turn(llm, topic=topic, history=history, question="а как их снизить?")
    assert second == "как снизить побочные эффекты леводопы"
    assert "побочные эффекты леводопы" in llm.prompts[1]
    assert topic_after == second


def test_a_self_contained_question_is_not_rewritten() -> None:
    question = "Какие немедикаментозные способы помогают при утренней скованности, если человек уже гуляет каждый день?"
    assert len(question) >= 80
    assert not is_followup(question)
    history = [
        {"role": "user", "content": "Расскажи про леводопу"},
        {"role": "assistant", "content": "Кратко."},
    ]
    query, topic = resolve_turn(object(), topic="Расскажи про леводопу", history=history, question=question)
    assert query == question
    assert topic == question


class _Store:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def similarity_search(self, query: str, k: int = 5) -> list:
        self.queries.append(query)
        return []


class _Answer:
    def __init__(self) -> None:
        self.messages: list | None = None

    def invoke(self, messages: list) -> _Reply:
        self.messages = messages
        return _Reply("Ответ по теме.")


def test_pipeline_searches_the_resolved_question_and_names_the_topic() -> None:
    store = _Store()
    llm = _Answer()
    history = [
        {"role": "user", "content": f"ход {index}"}
        for index in range(10)
    ]
    RAGPipeline(llm=llm, vector_store=store, prompt_text=lambda: "sys").query(
        "а как их снизить?",
        history=history,
        search_query="как снизить побочные эффекты леводопы",
        topic="леводопа",
        meaning="как снизить побочные эффекты леводопы",
    )
    assert store.queries == ["как снизить побочные эффекты леводопы"]
    assert llm.messages is not None
    history_turns = [message.content for message in llm.messages[1:-1]]
    assert history_turns == [f"ход {index}" for index in range(2, 10)]
    sent = llm.messages[-1].content
    assert "Тема диалога: леводопа" in sent
    assert "Смысл реплики с учётом диалога: как снизить побочные эффекты леводопы" in sent
    assert "Вопрос пользователя: а как их снизить?" in sent
