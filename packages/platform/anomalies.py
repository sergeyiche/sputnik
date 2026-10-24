"""Highlight a tenant day when usage or disk leaves the trial baseline."""

from __future__ import annotations

from packages.platform.store import UsageDay

DISK_LIMIT_BYTES = 500 * 1024 * 1024
QUESTION_FLOOR = 20
TOKEN_FLOOR = 20_000
FALLBACK_QUESTIONS = 10


def anomaly_reasons(today: UsageDay | None, previous: list[UsageDay], disk_bytes: int) -> list[str]:
    reasons: list[str] = []
    if today is not None and previous:
        questions = [day.questions for day in previous]
        tokens = [day.tokens_in + day.tokens_out for day in previous]
        avg_questions = sum(questions) / len(questions)
        avg_tokens = sum(tokens) / len(tokens)
        today_tokens = today.tokens_in + today.tokens_out
        spiked = today.questions > avg_questions * 3 or today_tokens > avg_tokens * 3
        loud = today.questions >= QUESTION_FLOOR or today_tokens >= TOKEN_FLOOR
        if spiked and loud and (avg_questions > 0 or avg_tokens > 0):
            reasons.append("Всплеск вопросов или токенов")
    if today is not None and today.questions >= FALLBACK_QUESTIONS:
        if today.empty_retrievals / today.questions > 0.5:
            reasons.append("Больше половины ответов без базы знаний")
    if today is not None and today.errors:
        reasons.append("Есть ошибки модели")
    if disk_bytes > DISK_LIMIT_BYTES:
        reasons.append("Каталог больше 500 МБ")
    return reasons
