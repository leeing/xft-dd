"""Helpers for JSON-shaped chat completion calls."""

from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from openai import OpenAIError

from xft.ai.json_extractor import extract_json
from xft.settings import settings

_RATE_LIMIT_LOCK: asyncio.Lock | None = None
_NEXT_ALLOWED_AT = 0.0


def _looks_like_unsupported_response_format(exc: OpenAIError) -> bool:
    text = str(exc).lower()
    return "response_format" in text and any(token in text for token in ("unsupported", "invalid", "unknown"))


def reset_llm_rate_limiter() -> None:
    """Reset process-local LLM rate limiter state for tests."""
    global _RATE_LIMIT_LOCK, _NEXT_ALLOWED_AT  # noqa: PLW0603
    _RATE_LIMIT_LOCK = None
    _NEXT_ALLOWED_AT = 0.0


async def create_json_chat_completion(client: Any, **kwargs: Any) -> Any:
    """Call chat completions with JSON mode, global pacing, and 429 retry."""
    attempts = max(1, int(settings.llm_rate_limit_max_retries) + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await _create_json_chat_completion_once(client, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if not _looks_like_rate_limit(exc) or attempt >= attempts - 1:
                raise
            last_exc = exc
            await asyncio.sleep(_retry_delay(attempt))
    if last_exc is not None:
        raise last_exc
    msg = "LLM call failed before any attempt was made"
    raise RuntimeError(msg)


async def _create_json_chat_completion_once(client: Any, **kwargs: Any) -> Any:
    await _throttle_llm_call()
    try:
        return await client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        if not _looks_like_unsupported_response_format(exc):
            raise
    await _throttle_llm_call()
    return await client.chat.completions.create(**kwargs)


async def _throttle_llm_call() -> None:
    global _RATE_LIMIT_LOCK, _NEXT_ALLOWED_AT  # noqa: PLW0603
    interval = float(settings.llm_rate_limit_min_interval_seconds)
    if interval <= 0:
        return
    if _RATE_LIMIT_LOCK is None:
        _RATE_LIMIT_LOCK = asyncio.Lock()
    async with _RATE_LIMIT_LOCK:
        now = monotonic()
        wait_seconds = max(0.0, _NEXT_ALLOWED_AT - now)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            now = monotonic()
        _NEXT_ALLOWED_AT = max(now, _NEXT_ALLOWED_AT) + interval


def _looks_like_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text


def _retry_delay(attempt: int) -> float:
    base = float(settings.llm_rate_limit_backoff_seconds)
    if base <= 0:
        return 0.0
    return float(base * (2**attempt))


async def parse_json_object_with_repair(
    *,
    client: Any,
    raw: str,
    model: str,
    timeout: int,
    target_description: str,
) -> tuple[dict[str, Any], str, bool]:
    """Parse a JSON object, asking the LLM once to repair malformed JSON."""
    try:
        parsed = json.loads(extract_json(raw))
    except json.JSONDecodeError:
        repaired_raw = await _repair_json_object(
            client=client,
            raw=raw,
            model=model,
            timeout=timeout,
            target_description=target_description,
        )
        repaired = json.loads(extract_json(repaired_raw))
        if not isinstance(repaired, dict):
            msg = "Repaired LLM response JSON must be an object"
            raise TypeError(msg) from None
        return repaired, repaired_raw, True
    if not isinstance(parsed, dict):
        msg = "LLM response JSON must be an object"
        raise TypeError(msg)
    return parsed, raw, False


async def _repair_json_object(
    *,
    client: Any,
    raw: str,
    model: str,
    timeout: int,
    target_description: str,
) -> str:
    system = (
        "你是严格的JSON修复器。只修复语法错误，不新增事实，不改字段含义。"
        "只输出一个合法JSON对象，不输出解释、Markdown或代码块。"
    )
    user = {
        "task": "把 malformed_json 修复为合法 JSON object。",
        "target_description": target_description,
        "rules": [
            "保留原字段和值的语义。",
            "缺少逗号、引号或括号时只做语法修复。",
            "无法确定时使用空字符串、空数组或 null，不要编造业务证据。",
        ],
        "malformed_json": raw,
    }
    resp = await create_json_chat_completion(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        temperature=0.0,
        timeout=timeout,
    )
    return str(resp.choices[0].message.content or "{}")
