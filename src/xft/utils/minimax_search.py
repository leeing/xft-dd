"""MiniMax Search API wrapper.

Calls /v1/coding_plan/search directly via async httpx.
Credentials are read exclusively through xft.settings (pydantic-settings).
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import structlog
from sqlalchemy.exc import SQLAlchemyError

from xft.core.search_models import SearchItem, make_item_id
from xft.settings import settings

log = structlog.get_logger(__name__)

_SEARCH_PATH = "/coding_plan/search"


def _search_url() -> str:
    """Derive the search endpoint from the configured base URL.

    base_url is typically https://api.minimaxi.chat/v1  →  strip /v1 then append path.
    """
    base = settings.minimax_base_url.rstrip("/")
    root = base[: base.rfind("/v1")] if "/v1" in base else base
    return f"{root}/v1{_SEARCH_PATH}"


def search_cache_params(*, max_results: int) -> dict[str, object]:
    """Return cache-affecting MiniMax Search parameters."""
    return {"endpoint": _search_url(), "max_results": max_results}


async def run_search(
    *,
    query: str,
    dimension_id: str,
    timeout: int = 30,
    max_results: int = 0,
) -> list[SearchItem]:
    """Call MiniMax /v1/coding_plan/search and return parsed SearchItems.

    max_results > 0 applies a local cap.  max_results <= 0 keeps all results
    returned by MiniMax.

    Raises:
        httpx.TimeoutException: if the request exceeds *timeout* seconds.
        httpx.HTTPStatusError: on non-2xx responses.
        ValueError: if the response body is not valid JSON / missing expected keys.
    """
    url = _search_url()
    cache_params = search_cache_params(max_results=max_results)
    if settings.cache_enabled is True and settings.search_cache_enabled is True:
        try:
            from xft.cache.repository import SearchCacheKey, SearchCacheRepo

            key = SearchCacheKey(provider="minimax", query_text=query, params=cache_params)
            cached = await SearchCacheRepo().get_items(key, dimension_id=dimension_id)
        except SQLAlchemyError as exc:
            log.warning("search_cache_read_failed", query=query[:40], error=str(exc))
        else:
            if cached is not None:
                log.debug("search_cache_hit", query=query[:40], items=len(cached))
                return cached

    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(url, headers=headers, json={"q": query})
        response.raise_for_status()

    data = response.json()
    organic = data.get("organic", [])
    if max_results > 0:
        organic = organic[:max_results]
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
    if settings.cache_enabled is True and settings.search_cache_enabled is True:
        try:
            from xft.cache.repository import SearchCacheKey, SearchCacheRepo

            key = SearchCacheKey(provider="minimax", query_text=query, params=cache_params)
            await SearchCacheRepo().put_success(key, raw_response=data, organic=organic)
        except SQLAlchemyError as exc:
            log.warning("search_cache_write_failed", query=query[:40], error=str(exc))
    return items


_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"from", "source", "spm"}


def normalize_url(url: str | None) -> str | None:
    """Normalize a URL for dedup comparison without mutating its semantics.

    - lowercase scheme and host
    - strip www. prefix
    - strip trailing / from path
    - strip tracking query params (utm_*, from, source, spm)
    - sort query params for deterministic comparison
    - preserve business query params (id, q, etc.)

    Returns None when url is None. Unparseable URLs are returned as-is (stripped).
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return url.strip()

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"

    query_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in _TRACKING_QUERY_KEYS:
            continue
        if key_lower.startswith(_TRACKING_QUERY_PREFIXES):
            continue
        query_pairs.append((key, value))

    query_pairs.sort()
    query = urlencode(query_pairs, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def dedup_items(items: list[SearchItem]) -> list[SearchItem]:
    """Deduplicate by normalized URL (preferred) or title+snippet when URL is absent.

    Uses normalize_url() so that minor URL variations (www, trailing slash,
    tracking params) are treated as the same item.  The first occurrence wins,
    meaning Metaso source items (prepended before MiniMax items) take priority.
    """
    seen: set[str] = set()
    result: list[SearchItem] = []
    for item in items:
        norm = normalize_url(item.url)
        key = f"url:{norm}" if norm else f"text:{item.title}{item.snippet}"
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
