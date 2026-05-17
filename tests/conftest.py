"""Shared pytest configuration. asyncio_mode = auto is set in pyproject.toml."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import xft.pipeline.diligence.nodes.summarize_node as sn
from xft.settings import settings


@pytest.fixture(autouse=True)
def reset_openai_client() -> Generator[None, None, None]:
    """Reset the AsyncOpenAI singleton before and after each test for isolation."""
    sn._ai_client = None
    yield
    sn._ai_client = None


@pytest.fixture(autouse=True)
def disable_sql_cache_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests isolated from a developer's real .env cache settings."""
    monkeypatch.setattr(settings, "cache_enabled", False)
