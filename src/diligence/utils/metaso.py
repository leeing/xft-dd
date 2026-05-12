"""秘塔 AI 搜索 (metaso.cn) 客户端。

两种模式：
  1. chat 模式 (默认) — POST /api/v1/chat/completions
     AI 综合答案，适合需要跨源判断的维度 (basic_info, background, tech_cert)
  2. search 模式 — POST /api/v1/search
     网页搜索结果，返回真实 URL + rawContent，适合需要原始数据的维度 (ip, product, scale)

chat 模式：
  POST https://metaso.cn/api/v1/chat/completions
  {"q": "查询词", "model": "fast_thinking", "format": "simple", "conciseSnippet": true}
  返回：{"answer": "...", "sources": [...], "credits": N}

search 模式：
  POST https://metaso.cn/api/v1/search
  {"q": "查询词", "scope": "webpage", "includeSummary": true, "includeRawContent": true, "size": "5"}
  返回：{"webpages": [{"title":..., "link":..., "summary":..., "content":...}], "credits": N}
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from diligence.models import SearchItem, make_item_id

log = structlog.get_logger(__name__)

_METASO_HOST = "metaso.cn"
_METASO_CHAT_PATH = "/api/v1/chat/completions"
_METASO_SEARCH_PATH = "/api/v1/search"
_METASO_MODEL = "fast_thinking"

# 去掉 fast_thinking 内部思维链（> 开头）和引用标号（[[1]]）
_THINK_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_CITE_RE = re.compile(r"\[\[\d+\]\]")


def _clean_answer(raw: str) -> str:
    """Remove thinking-chain lines and citation markers from metaso answer."""
    cleaned = _THINK_LINE_RE.sub("", raw)
    cleaned = _CITE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ── chat mode ─────────────────────────────────────────────────────────────────


async def query_metaso(
    api_key: str, query: str, timeout: int = 30, *, verify_tls: bool = True
) -> tuple[str, list[dict], int]:
    """Query metaso chat API. Returns (cleaned_answer, sources, credits)."""
    url = f"https://{_METASO_HOST}{_METASO_CHAT_PATH}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "model": _METASO_MODEL,
        "format": "simple",
        "conciseSnippet": True,
    }
    async with httpx.AsyncClient(timeout=timeout, verify=verify_tls, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if "answer" not in data:
        log.warning("metaso_no_answer", query=query[:60], keys=list(data.keys()))
        return "", [], 0
    credits: int = data.get("credits", 0)
    sources: list[dict] = data.get("sources", [])
    return _clean_answer(data["answer"]), sources, credits


def make_metaso_item(answer: str, query: str, dimension_id: str) -> SearchItem:
    """Wrap a metaso chat answer as a SearchItem with full_text populated."""
    url = f"metaso://search?q={query[:80]}"
    return SearchItem(
        id=make_item_id(url=url, title=f"秘塔AI搜索: {query[:40]}", snippet=answer[:200]),
        title=query[:80],
        url=url,
        snippet=answer[:300],
        full_text=answer,
        query=query,
        dimension_id=dimension_id,
        source="metaso_chat",
        rank=0,
        fetched_at=datetime.now(UTC),
    )


def make_metaso_source_items(
    sources: list[dict[str, Any]],
    query: str,
    dimension_id: str,
) -> list[SearchItem]:
    """Convert metaso chat sources to SearchItems with real URLs for crawl4ai enrichment.

    Each source has: title, link (real URL), summary or snippet, date.
    full_text is left empty — crawl4ai will populate it.
    """
    items: list[SearchItem] = []
    for i, src in enumerate(sources):
        link: str = src.get("link", "")
        if not link:
            continue
        title: str = src.get("title", "")
        summary: str = src.get("summary") or src.get("snippet", "")
        items.append(
            SearchItem(
                id=make_item_id(url=link, title=title, snippet=summary[:200]),
                title=title,
                url=link,
                snippet=summary[:300],
                full_text="",
                query=query,
                dimension_id=dimension_id,
                source="metaso_chat",
                rank=i,
                fetched_at=datetime.now(UTC),
            )
        )
    return items


async def fetch_metaso_items(  # noqa: PLR0913
    dimension_id: str,
    queries: list[str],
    api_key: str,
    *,
    concurrency: int = 2,
    timeout: int = 30,
    verify_tls: bool = True,
) -> tuple[list[SearchItem], list[SearchItem], int, int, int]:
    """Query metaso chat for each query string.

    Returns (answer_items, source_items, success, failed, total_credits).
    """
    if not api_key or not queries:
        return [], [], 0, 0, 0

    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(query: str) -> tuple[SearchItem | None, list[SearchItem], int, bool]:
        async with semaphore:
            try:
                answer, sources, credits = await asyncio.wait_for(
                    query_metaso(api_key, query, timeout, verify_tls=verify_tls),
                    timeout=timeout + 5,
                )
                source_items = make_metaso_source_items(sources, query, dimension_id)
                if not answer or len(answer) < 20:  # noqa: PLR2004
                    log.warning("metaso_short_answer", query=query[:60], chars=len(answer))
                    return None, source_items, credits, False
                log.debug("metaso_ok", query=query[:60], chars=len(answer))
                return make_metaso_item(answer, query, dimension_id), source_items, credits, True
            except (TimeoutError, OSError, httpx.HTTPError, ValueError) as exc:
                log.warning("metaso_failed", query=query[:60], error=str(exc))
                return None, [], 0, False

    raw_results = await asyncio.gather(*[fetch_one(q) for q in queries])
    answer_items = [item for item, _, _, _ in raw_results if item is not None]
    source_items: list[SearchItem] = []
    for _, srcs, _, _ in raw_results:
        source_items.extend(srcs)
    success_count = sum(1 for _, _, _, ok in raw_results if ok)
    failed_count = sum(1 for _, _, _, ok in raw_results if not ok)
    total_credits = sum(c for _, _, c, _ in raw_results)

    if answer_items:
        log.info("metaso_enriched", dimension=dimension_id, count=len(answer_items), credits=total_credits)

    return answer_items, source_items, success_count, failed_count, total_credits


async def enrich_with_metaso(
    items: list[SearchItem],
    dimension_id: str,
    queries: list[str],
    api_key: str,
    *,
    verify_tls: bool = True,
) -> tuple[list[SearchItem], int, int, int]:
    """Fetch metaso chat items and prepend them (with source URLs) to existing items list."""
    answer_items, source_items, success, failed, credits = await fetch_metaso_items(
        dimension_id, queries, api_key, verify_tls=verify_tls
    )
    return [*source_items, *answer_items, *items], success, failed, credits


# ── search mode ───────────────────────────────────────────────────────────────


async def query_metaso_search(  # noqa: PLR0913
    api_key: str,
    query: str,
    *,
    size: int = 5,
    include_raw_content: bool = True,
    timeout: int = 30,
    verify_tls: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Query metaso /api/v1/search. Returns (webpage_dicts, credits)."""
    url = f"https://{_METASO_HOST}{_METASO_SEARCH_PATH}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "q": query,
        "scope": "webpage",
        "includeSummary": True,
        "size": str(size),
        "includeRawContent": include_raw_content,
        "conciseSnippet": False,
    }
    async with httpx.AsyncClient(timeout=timeout, verify=verify_tls, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    webpages: list[dict[str, Any]] = data.get("webpages", [])
    credits: int = data.get("credits", 0)
    return webpages, credits


def make_metaso_search_item(wp: dict[str, Any], query: str, dimension_id: str, rank: int) -> SearchItem:
    """Convert a metaso search webpage result to a SearchItem.

    Uses raw content as full_text when available; falls back to summary or snippet.
    The URL is a real http(s) link, eligible for crawl4ai enrichment.
    """
    link: str = wp.get("link", "")
    title: str = wp.get("title", "")
    summary: str = wp.get("summary", "")
    content: str = wp.get("content", "")
    snippet: str = wp.get("snippet", "")

    # best available full text: raw content > summary > snippet
    full_text = content or summary or snippet
    # snippet for display: summary (AI-generated, most informative) > snippet > content[:300]
    display_snippet = summary or snippet or content[:300]

    return SearchItem(
        id=make_item_id(url=link, title=title, snippet=display_snippet[:200]),
        title=title,
        url=link,
        snippet=display_snippet[:300],
        full_text=full_text,
        query=query,
        dimension_id=dimension_id,
        source="metaso_search",
        rank=rank,
        fetched_at=datetime.now(UTC),
    )


async def fetch_metaso_search_items(  # noqa: PLR0913
    dimension_id: str,
    queries: list[str],
    api_key: str,
    *,
    size: int = 5,
    include_raw_content: bool = True,
    concurrency: int = 2,
    timeout: int = 30,
    verify_tls: bool = True,
) -> tuple[list[SearchItem], int, int, int]:
    """Query metaso search for each query; returns (items, success, failed, credits).

    Each query produces up to `size` SearchItems with real URLs and raw content.
    Items are interleaved by rank so results from different queries are mixed.
    """
    if not api_key or not queries:
        return [], 0, 0, 0

    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(query: str) -> tuple[list[SearchItem], int, bool]:
        async with semaphore:
            try:
                webpages, credits = await asyncio.wait_for(
                    query_metaso_search(
                        api_key,
                        query,
                        size=size,
                        include_raw_content=include_raw_content,
                        timeout=timeout,
                        verify_tls=verify_tls,
                    ),
                    timeout=timeout + 5,
                )
            except (TimeoutError, OSError, httpx.HTTPError, ValueError) as exc:
                log.warning("metaso_search_failed", query=query[:60], error=str(exc))
                return [], 0, False
            else:
                if not webpages:
                    log.warning("metaso_search_empty", query=query[:60])
                    return [], credits, False
                items = [make_metaso_search_item(wp, query, dimension_id, rank=i) for i, wp in enumerate(webpages)]
                log.debug("metaso_search_ok", query=query[:60], results=len(items))
                return items, credits, True

    raw_results = await asyncio.gather(*[fetch_one(q) for q in queries])

    # Interleave results by rank: all rank-0 items first, then rank-1, etc.
    all_items: list[SearchItem] = []
    for rank in range(size):
        for items, _, _ in raw_results:
            if rank < len(items):
                all_items.append(items[rank])

    success_count = sum(1 for _, _, ok in raw_results if ok)
    failed_count = sum(1 for _, _, ok in raw_results if not ok)
    total_credits = sum(c for _, c, _ in raw_results)

    if all_items:
        log.info("metaso_search_enriched", dimension=dimension_id, count=len(all_items), credits=total_credits)

    return all_items, success_count, failed_count, total_credits


async def enrich_with_metaso_search(  # noqa: PLR0913
    items: list[SearchItem],
    dimension_id: str,
    queries: list[str],
    api_key: str,
    *,
    size: int = 5,
    verify_tls: bool = True,
) -> tuple[list[SearchItem], int, int, int]:
    """Fetch metaso search items and prepend them to existing items list."""
    metaso_items, success, failed, credits = await fetch_metaso_search_items(
        dimension_id, queries, api_key, size=size, verify_tls=verify_tls
    )
    return [*metaso_items, *items], success, failed, credits
