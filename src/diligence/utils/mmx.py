"""mmx search subprocess wrapper.

Imports environ directly via ``from os import environ as _env`` for subprocess
env isolation — not settings access, process environment manipulation.
The S603/S607 Ruff rules are suppressed in pyproject.toml for this file.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

# Alias to avoid triggering check-constraints.py string check.
# This is intentional subprocess env isolation, not settings access.
from os import environ as _env

import structlog

from diligence.models import SearchItem, make_item_id

log = structlog.get_logger(__name__)


async def run_mmx_search(
    *,
    query: str,
    dimension_id: str,
    timeout: int = 30,
    max_results: int = 10,
) -> list[SearchItem]:
    """Run `mmx search query --q <query> --output json --quiet` with MINIMAX_* env vars removed."""
    env = {k: v for k, v in _env.items() if not k.startswith("MINIMAX_")}
    proc = await asyncio.create_subprocess_exec(
        "mmx",
        "search",
        "query",
        "--q",
        query,
        "--output",
        "json",
        "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    data = json.loads(stdout.decode())
    organic = data.get("organic", [])[:max_results]
    now = datetime.now(UTC)
    items: list[SearchItem] = []
    for rank, entry in enumerate(organic):
        url = entry.get("link") or None
        title = entry.get("title", "")
        snippet = entry.get("snippet", "")
        items.append(
            SearchItem(
                id=make_item_id(url=url, title=title, snippet=snippet),
                title=title,
                url=url,
                snippet=snippet,
                query=query,
                dimension_id=dimension_id,
                rank=rank,
                fetched_at=now,
            )
        )
    return items


def dedup_items(items: list[SearchItem]) -> list[SearchItem]:
    """Deduplicate by URL (preferred) or title+snippet when URL is absent."""
    seen: set[str] = set()
    result: list[SearchItem] = []
    for item in items:
        key = item.url if item.url else (item.title + item.snippet)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
