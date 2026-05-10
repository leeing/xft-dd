"""Shared pytest configuration. asyncio_mode = auto is set in pyproject.toml."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import diligence.nodes.summarize_node as sn


@pytest.fixture(autouse=True)
def reset_openai_client() -> Generator[None, None, None]:
    """Reset the AsyncOpenAI singleton before and after each test for isolation."""
    sn._ai_client = None
    yield
    sn._ai_client = None
