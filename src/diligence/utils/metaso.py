"""秘塔 AI 搜索 (metaso.cn) 客户端。

秘塔是带联网搜索的 AI 问答引擎，能直接访问企查查、启信宝等企业数据库，
返回经过 AI 综合的自然语言答案，而非原始搜索结果列表。

对接方式：
  POST https://metaso.cn/api/v1/chat/completions
  {"q": "查询词", "model": "fast_thinking", "format": "simple", "conciseSnippet": true}
  返回：{"answer": "...", "sources": [...], "credits": N}

集成策略：
  - 每个维度用一条精准的中文查询，直接问「{target}的{字段}是什么」
  - 清理 fast_thinking 的内部思维链（> 开头的行）和引用编号（[[1]]）
  - 把答案作为一个 full_text 已填充的 SearchItem 注入，可信度权重高于 MiniMax Search snippets
  - 秘塔结果放在该维度 items 列表最前面，确保 summarize_node 优先使用
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import httpx
import structlog

from diligence.models import SearchItem, make_item_id

log = structlog.get_logger(__name__)

_METASO_HOST = "metaso.cn"
_METASO_PATH = "/api/v1/chat/completions"
_METASO_MODEL = "fast_thinking"

# 去掉 fast_thinking 内部思维链（> 开头）和引用标号（[[1]]）
_THINK_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)
_CITE_RE = re.compile(r"\[\[\d+\]\]")


def _clean_answer(raw: str) -> str:
    """Remove thinking-chain lines and citation markers from metaso answer."""
    cleaned = _THINK_LINE_RE.sub("", raw)
    cleaned = _CITE_RE.sub("", cleaned)
    # collapse multiple blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


async def query_metaso(api_key: str, query: str, timeout: int = 30) -> tuple[str, int]:
    """Query metaso AI search API. Returns (cleaned_answer, credits)."""
    url = f"https://{_METASO_HOST}{_METASO_PATH}"
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
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:  # noqa: S501
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if "answer" not in data:
        log.warning("metaso_no_answer", query=query[:60], keys=list(data.keys()))
        return "", 0
    credits: int = data.get("credits", 0)
    return _clean_answer(data["answer"]), credits


def make_metaso_item(answer: str, query: str, dimension_id: str) -> SearchItem:
    """Wrap a metaso answer as a SearchItem with full_text populated."""
    url = f"metaso://search?q={query[:80]}"
    return SearchItem(
        id=make_item_id(url=url, title=f"秘塔AI搜索: {query[:40]}", snippet=answer[:200]),
        title=f"【秘塔AI】{query[:60]}",
        url=url,
        snippet=answer[:300],
        full_text=answer,
        query=query,
        dimension_id=dimension_id,
        rank=0,  # rank 0 = placed first in list
        fetched_at=datetime.now(UTC),
    )


async def fetch_metaso_items(
    dimension_id: str,
    queries: list[str],
    api_key: str,
    *,
    concurrency: int = 2,
    timeout: int = 30,
) -> tuple[list[SearchItem], int]:
    """Query metaso for each query string and return (metaso_items, total_credits).

    Args:
        dimension_id: Dimension ID for tagging SearchItems.
        queries: List of query strings with {target} already substituted.
        api_key: Metaso Bearer key (without "Bearer " prefix).
        concurrency: Max parallel metaso requests.
        timeout: Per-request timeout in seconds.

    Returns:
        Tuple of (list of metaso SearchItems, total credits consumed).
    """
    if not api_key or not queries:
        return [], 0

    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(query: str) -> tuple[SearchItem | None, int]:
        async with semaphore:
            try:
                answer, credits = await asyncio.wait_for(
                    query_metaso(api_key, query, timeout),
                    timeout=timeout + 5,
                )
                if not answer or len(answer) < 20:  # noqa: PLR2004
                    log.warning("metaso_short_answer", query=query[:60], chars=len(answer))
                    return None, credits
                log.debug("metaso_ok", query=query[:60], chars=len(answer))
                return make_metaso_item(answer, query, dimension_id), credits
            except (TimeoutError, OSError, httpx.HTTPError, ValueError) as exc:
                log.warning("metaso_failed", query=query[:60], error=str(exc))
                return None, 0

    raw_results = await asyncio.gather(*[fetch_one(q) for q in queries])
    metaso_items = [item for item, _ in raw_results if item is not None]
    total_credits = sum(c for _, c in raw_results)

    if metaso_items:
        log.info("metaso_enriched", dimension=dimension_id, count=len(metaso_items), credits=total_credits)

    return metaso_items, total_credits


async def enrich_with_metaso(
    items: list[SearchItem],
    dimension_id: str,
    queries: list[str],
    api_key: str,
) -> tuple[list[SearchItem], int]:
    """Fetch metaso items and prepend them to existing items list.

    Convenience wrapper around fetch_metaso_items() that handles the prepend.
    Kept at 4 positional args to stay within PLR0913 limit.
    Concurrency and timeout use fetch_metaso_items() defaults (2, 30).

    Returns:
        Tuple of (enriched items list, total credits consumed).
    """
    metaso_items, credits = await fetch_metaso_items(dimension_id, queries, api_key)
    return [*metaso_items, *items], credits
