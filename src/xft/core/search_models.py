"""Shared search result models used by Web, cache, and pipeline layers."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


def make_item_id(*, url: str | None, title: str, snippet: str) -> str:
    """Stable 12-char sha1 ID: prefer URL, fallback to title+snippet."""
    key = url if url else (title + snippet)
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


class SearchItem(BaseModel):
    """Single search result from MiniMax Search, Metaso, or another provider."""

    id: str
    title: str
    url: str | None = None
    snippet: str
    full_text: str = ""
    query: str
    dimension_id: str
    source: Literal["minimax", "metaso_chat", "metaso_search"] = "minimax"
    rank: int | None = None
    fetched_at: datetime


class DimensionSearchResult(BaseModel):
    """Aggregated search results for one analysis dimension."""

    dimension_id: str
    dimension_name: str
    status: Literal["success", "partial", "failed"]
    items: list[SearchItem]
    error: str | None = None
    extractions: dict[str, object] | None = None
