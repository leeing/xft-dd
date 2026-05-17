"""Configurable product recommendation pipeline."""

from __future__ import annotations

from typing import Any

__all__ = ["run_recommendation"]


async def run_recommendation(*args: Any, **kwargs: Any) -> Any:
    """Run recommendation pipeline with a lazy import to avoid package cycles."""
    from xft.pipeline.recommender.graph import run_recommendation as _run_recommendation

    return await _run_recommendation(*args, **kwargs)
