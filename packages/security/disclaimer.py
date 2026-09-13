MEDICAL_DISCLAIMER = (
    "⚠️ Этот ассистент предоставляет общую справочную информацию о болезни Паркинсона "
    "и не заменяет консультацию врача. Не используйте ответы для самодиагностики или "
    "изменения назначенного лечения. При острых симптомах обратитесь к специалисту."
)


def append_disclaimer(answer: str) -> str:
    return f"{answer.strip()}\n\n---\n{MEDICAL_DISCLAIMER}"


JAILBREAK_PATTERNS = (
    "ignore previous",
    "ignore all instructions",
    "забудь инструкции",
    "игнорируй инструкции",
    "system prompt",
    "jailbreak",
)


def is_suspicious_query(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in JAILBREAK_PATTERNS)
