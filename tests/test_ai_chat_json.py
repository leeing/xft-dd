from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError

from xft.ai import chat_json
from xft.ai.chat_json import create_json_chat_completion, parse_json_object_with_repair
from xft.settings import settings


def _response(content: str) -> object:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.completions = _FakeCompletions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


async def test_create_json_chat_completion_retries_429_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            OpenAIError("Error code: 429 rate_limit"),
            OpenAIError("Error code: 429 rate_limit"),
            _response('{"ok": true}'),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(settings, "llm_rate_limit_min_interval_seconds", 0)
    monkeypatch.setattr(settings, "llm_rate_limit_max_retries", 3)
    monkeypatch.setattr(settings, "llm_rate_limit_backoff_seconds", 0.1)
    monkeypatch.setattr(chat_json.asyncio, "sleep", fake_sleep)
    chat_json.reset_llm_rate_limiter()

    resp = await create_json_chat_completion(client, model="m", messages=[], temperature=0)

    assert resp.choices[0].message.content == '{"ok": true}'
    assert len(client.completions.calls) == 3
    assert sleeps == [0.1, 0.2]


async def test_parse_json_object_with_repair_uses_one_repair_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_response('{"result": "matched", "evidence": ["修复后"]}')])
    monkeypatch.setattr(settings, "llm_rate_limit_min_interval_seconds", 0)
    chat_json.reset_llm_rate_limiter()

    parsed, raw, repaired = await parse_json_object_with_repair(
        client=client,
        raw='{"result": "matched" "evidence": ["坏JSON"]}',
        model="m",
        timeout=10,
        target_description="测试JSON",
    )

    assert repaired is True
    assert parsed == {"result": "matched", "evidence": ["修复后"]}
    assert raw == '{"result": "matched", "evidence": ["修复后"]}'
    assert len(client.completions.calls) == 1
    assert "malformed_json" in client.completions.calls[0]["messages"][1]["content"]
