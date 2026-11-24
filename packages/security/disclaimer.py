MEDICAL_DISCLAIMER = (
    "⚠️ Этот ассистент предоставляет общую справочную информацию о болезни Паркинсона "
    "и не заменяет консультацию врача. Не используйте ответы для самодиагностики или "
    "изменения назначенного лечения. При острых симптомах обратитесь к специалисту."
)


def append_disclaimer(answer: str, disclaimer: str | None = None) -> str:
    text = (disclaimer or MEDICAL_DISCLAIMER).strip()
    return f"{answer.strip()}\n\n---\n{text}"


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


CRISIS_PATTERNS = (
    "не вижу смысла жить",
    "нет смысла жить",
    "не хочу жить",
    "хочу умереть",
    "лучше умереть",
    "покончить с собой",
    "покончить жизнь",
    "убить себя",
    "суицид",
)

CRISIS_ANSWER = (
    "Мне очень жаль, что вам сейчас так тяжело. Пожалуйста, не оставайтесь "
    "с этим один на один: позвоните 103 или 112, а если рядом есть близкий "
    "человек — попросите его побыть с вами. Если вы уже думали о том, чтобы "
    "причинить себе вред, скажите об этом врачу или специалисту скорой помощи "
    "прямо сейчас. Вы важны, и помощь можно получить."
)


def is_crisis_query(text: str) -> bool:
    lowered = text.casefold().replace("ё", "е")
    return any(pattern in lowered for pattern in CRISIS_PATTERNS)
