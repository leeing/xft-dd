"""Helpers for JSON-shaped chat completion calls."""

from __future__ import annotations

from typing import Any

from openai import OpenAIError


def _looks_like_unsupported_response_format(exc: OpenAIError) -> bool:
    text = str(exc).lower()
    return "response_format" in text and any(token in text for token in ("unsupported", "invalid", "unknown"))


async def create_json_chat_completion(client: Any, **kwargs: Any) -> Any:
    """Call chat completions with JSON mode, retrying without it if unsupported."""
    try:
        return await client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        if not _looks_like_unsupported_response_format(exc):
            raise
    return await client.chat.completions.create(**kwargs)
