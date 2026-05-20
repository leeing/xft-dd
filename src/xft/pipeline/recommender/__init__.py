"""Configurable business recommendation pipeline."""

from __future__ import annotations

from typing import Any

from xft.constants import DEFAULT_SCENARIO, DEFAULT_WAREHOUSE

__all__ = ["run_recommendation", "DEFAULT_SCENARIO", "DEFAULT_WAREHOUSE"]


async def run_recommendation(*args: Any, **kwargs: Any) -> Any:
    """Run recommendation pipeline with a lazy import to avoid package cycles."""
    from xft.pipeline.recommender.graph import run_recommendation as _run_recommendation

    return await _run_recommendation(*args, **kwargs)
