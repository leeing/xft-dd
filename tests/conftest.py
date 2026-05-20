"""Shared pytest configuration. asyncio_mode = auto is set in pyproject.toml."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from xft.ai.client import reset_ai_client
from xft.settings import settings


@pytest.fixture(autouse=True)
def reset_openai_client() -> Generator[None, None, None]:
    """Reset the AsyncOpenAI singleton before and after each test for isolation."""
    reset_ai_client()
    yield
    reset_ai_client()


@pytest.fixture(autouse=True)
def disable_sql_cache_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests isolated from a developer's real .env cache settings."""
    monkeypatch.setattr(settings, "cache_enabled", False)
