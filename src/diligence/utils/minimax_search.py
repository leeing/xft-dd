"""MiniMax Search API wrapper.

Calls /v1/coding_plan/search directly via async httpx.
Credentials are read exclusively through diligence.settings (pydantic-settings).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from diligence.models import SearchItem, make_item_id
from diligence.settings import settings

log = structlog.get_logger(__name__)

_SEARCH_PATH = "/coding_plan/search"


def _search_url() -> str:
    """Derive the search endpoint from the configured base URL.

    base_url is typically https://api.minimaxi.chat/v1  →  strip /v1 then append path.
    """
    base = settings.minimax_base_url.rstrip("/")
    root = base[: base.rfind("/v1")] if "/v1" in base else base
    return f"{root}/v1{_SEARCH_PATH}"


async def run_search(
    *,
    query: str,
    dimension_id: str,
    timeout: int = 30,
    max_results: int = 10,
) -> list[SearchItem]:
    """Call MiniMax /v1/coding_plan/search and return parsed SearchItems.

    Raises:
        httpx.TimeoutException: if the request exceeds *timeout* seconds.
        httpx.HTTPStatusError: on non-2xx responses.
        ValueError: if the response body is not valid JSON / missing expected keys.
    """
    url = _search_url()
    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json={"q": query})
        response.raise_for_status()

    data = response.json()
    organic = data.get("organic", [])[:max_results]
    now = datetime.now(UTC)

    items: list[SearchItem] = []
    for rank, entry in enumerate(organic):
        url_val = entry.get("link") or None
        title = entry.get("title", "")
        snippet = entry.get("snippet", "")
        items.append(
            SearchItem(
                id=make_item_id(url=url_val, title=title, snippet=snippet),
                title=title,
                url=url_val,
                snippet=snippet,
                query=query,
                dimension_id=dimension_id,
                source="minimax",
                rank=rank,
                fetched_at=now,
            )
        )

    log.debug("search_done", query=query[:40], items=len(items))
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
