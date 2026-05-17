"""Legacy enterprise due diligence pipeline, now hosted as an XFT scenario."""

from __future__ import annotations

from typing import Any

__all__ = ["run_company_graph"]


async def run_company_graph(*args: Any, **kwargs: Any) -> Any:
    """Run the diligence pipeline with a lazy import to avoid package cycles."""
    from xft.pipeline.diligence.graph import run_company_graph as _run_company_graph

    return await _run_company_graph(*args, **kwargs)
