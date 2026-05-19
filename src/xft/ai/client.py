"""OpenAI-compatible client factory for LLM tasks."""

from __future__ import annotations

import httpx
from openai import AsyncOpenAI

from xft.settings import settings

_ai_client: AsyncOpenAI | None = None


def get_ai_client() -> AsyncOpenAI:
    """Return a cached AsyncOpenAI-compatible client.

    Uses LLM_* env vars when set; falls back to MINIMAX_* for backward compatibility.
    """
    global _ai_client  # noqa: PLW0603
    if _ai_client is None:
        api_key = settings.llm_api_key or settings.minimax_api_key
        _ai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.llm_base_url,
            max_retries=2,
            http_client=httpx.AsyncClient(trust_env=False),
        )
    return _ai_client


def reset_ai_client() -> None:
    """Clear the cached client; useful for tests that patch settings."""
    global _ai_client  # noqa: PLW0603
    _ai_client = None
