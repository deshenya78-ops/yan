import asyncio
import json

import pytest

from services.ai_replacements import AIReplacementError, build_ai_replacement_plan


class _FakeMessage:
    content = json.dumps({"replacements": {"OLD": "NEW"}, "notes": ["ok"]})


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


class _FakeCompletions:
    async def create(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        assert "Текст шаблона DOCX" in kwargs["messages"][1]["content"]
        return _FakeResponse()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()

    def __init__(self, api_key):
        assert api_key == "key"


def test_build_ai_replacement_plan_parses_json(monkeypatch):
    monkeypatch.setattr("services.ai_replacements.AsyncOpenAI", _FakeClient)

    plan = asyncio.run(build_ai_replacement_plan(
        api_key="key",
        model="test-model",
        template_text="OLD",
        user_text="replace with NEW",
    ))

    assert plan.replacements == {"OLD": "NEW"}
    assert plan.notes == ("ok",)


class _EmptyMessage:
    content = json.dumps({"replacements": {}})


class _EmptyChoice:
    message = _EmptyMessage()


class _EmptyResponse:
    choices = [_EmptyChoice()]


class _EmptyCompletions:
    async def create(self, **kwargs):
        return _EmptyResponse()


class _EmptyChat:
    completions = _EmptyCompletions()


class _EmptyClient:
    chat = _EmptyChat()

    def __init__(self, api_key):
        pass


def test_build_ai_replacement_plan_rejects_empty_plan(monkeypatch):
    monkeypatch.setattr("services.ai_replacements.AsyncOpenAI", _EmptyClient)

    with pytest.raises(AIReplacementError):
        asyncio.run(build_ai_replacement_plan(
            api_key="key",
            model="test-model",
            template_text="OLD",
            user_text="nothing",
        ))
