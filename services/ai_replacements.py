from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAIError


@dataclass(frozen=True)
class AIReplacementPlan:
    replacements: dict[str, str]
    notes: tuple[str, ...] = ()


class AIReplacementError(RuntimeError):
    """Raised when AI replacement planning fails."""


SYSTEM_PROMPT = """Ты помощник для заполнения договоров.
Тебе дадут текст DOCX-шаблона и свободное сообщение пользователя с реквизитами,
суммой, датой, номером договора и другими данными.

Твоя задача — вернуть JSON с точными текстовыми заменами.
Правила:
1. Ключи replacements должны быть ТОЧНЫМИ фрагментами из текста шаблона.
2. Значения replacements — новый текст, который нужно вставить.
3. Не придумывай данные, которых нет в сообщении пользователя.
4. Не заменяй общие слова вроде "Договор", "Заказчик", "Исполнитель" без явной причины.
5. Если не уверен, не добавляй замену и напиши пояснение в notes.
6. Ответ должен быть только валидным JSON в формате:
{"replacements":{"старый текст":"новый текст"},"notes":["пояснение"]}
"""


async def build_ai_replacement_plan(
    *,
    api_key: str,
    model: str,
    template_text: str,
    user_text: str,
) -> AIReplacementPlan:
    client = AsyncOpenAI(api_key=api_key)
    try:
        response = await client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Текст шаблона DOCX:\n"
                        f"{template_text[:18000]}\n\n"
                        "Свободные данные пользователя:\n"
                        f"{user_text}\n\n"
                        "Верни JSON с replacements и notes."
                    ),
                },
            ],
        )
    except OpenAIError as error:
        raise AIReplacementError(f"OpenAI API не смог построить план замен: {error}") from error
    content = response.choices[0].message.content
    if not content:
        raise AIReplacementError("OpenAI не вернул план замен.")

    try:
        payload: dict[str, Any] = json.loads(content)
    except json.JSONDecodeError as error:
        raise AIReplacementError("OpenAI вернул невалидный JSON с планом замен.") from error

    replacements_raw = payload.get("replacements", {})
    if not isinstance(replacements_raw, dict):
        raise AIReplacementError("OpenAI вернул replacements не в формате объекта JSON.")

    replacements = {
        str(source).strip(): str(target).strip()
        for source, target in replacements_raw.items()
        if str(source).strip()
    }
    notes_raw = payload.get("notes", [])
    notes = tuple(str(item) for item in notes_raw) if isinstance(notes_raw, list) else ()

    if not replacements:
        raise AIReplacementError(
            "Не получилось автоматически определить замены. Пришлите больше данных или используйте формат `старый текст => новый текст`."
        )

    return AIReplacementPlan(replacements=replacements, notes=notes)
