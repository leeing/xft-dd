"""Crawler mode: build L1 MiniMax and L2 crawl4ai caches without LLM reporting."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import cast

import httpx
import structlog

from xft.cache.hashing import stable_hash
from xft.cache.repository import SearchCacheRepo
from xft.pipeline.diligence.config import AppConfig, Dimension, validate_dimension_ids
from xft.settings import settings
from xft.utils.fetch import enrich_items
from xft.utils.minimax_search import dedup_items, run_search, search_cache_params

log = structlog.get_logger(__name__)


@dataclass
class CrawlerStats:
    targets: int = 0
    queries_total: int = 0
    l1_hits: int = 0
    l1_misses: int = 0
    search_failed: int = 0
    search_items: int = 0
    urls_considered: int = 0
    full_text_items: int = 0

    def add(self, other: CrawlerStats) -> None:
        self.targets += other.targets
        self.queries_total += other.queries_total
        self.l1_hits += other.l1_hits
        self.l1_misses += other.l1_misses
        self.search_failed += other.search_failed
        self.search_items += other.search_items
        self.urls_considered += other.urls_considered
        self.full_text_items += other.full_text_items


def active_dimensions(
    config: AppConfig,
    only: list[str] | None,
    skip: list[str] | None,
) -> tuple[list[Dimension], str | None]:
    """Return enabled dimensions filtered by --only/--skip, or an error message."""
    dims = [d for d in config.dimensions if d.enabled]
    if only:
        if err := validate_dimension_ids(only, config.dimensions, label="--only"):
            return [], err
        dims = [d for d in dims if d.id in only]
    if skip:
        if err := validate_dimension_ids(skip, config.dimensions, label="--skip"):
            return [], err
        dims = [d for d in dims if d.id not in skip]
    if not dims:
        return [], "error: no active dimensions after filtering"
    return dims, None


async def _crawl_query(
    query: str,
    dim: Dimension,
    target: str,
    config: AppConfig,
    cached_hashes: set[str],
) -> CrawlerStats:
    stats = CrawlerStats(queries_total=1)
    qhash = stable_hash(query)
    if qhash in cached_hashes:
        stats.l1_hits = 1
        sys.stderr.write(f"  [{dim.name}] L1 hit, skip: {query}\n")
        return stats

    stats.l1_misses = 1
    sys.stderr.write(f"  [{dim.name}] L1 miss, search+fetch: {query}\n")
    try:
        items = await run_search(
            query=query,
            dimension_id=dim.id,
            timeout=config.search_timeout_seconds,
            max_results=config.max_results_per_query,
        )
    except httpx.TimeoutException:
        stats.search_failed = 1
        log.warning("crawler_search_timeout", dimension=dim.id, query=query)
        sys.stderr.write(f"  [{dim.name}] search timeout: {query}\n")
        return stats
    except (httpx.HTTPError, ValueError) as exc:
        stats.search_failed = 1
        log.warning("crawler_search_error", dimension=dim.id, query=query, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] search error: {query} -- {exc}\n")
        return stats

    deduped = dedup_items(items)
    stats.search_items = len(items)
    stats.urls_considered = sum(1 for item in deduped if item.url)
    try:
        enriched = await enrich_items(
            deduped,
            blocked_domains=config.fetch_blocked_domains,
            target=target,
            fetch_timeout=config.crawl_fetch_timeout,
            concurrency=config.crawl_fetch_concurrency,
            max_full_text_chars=config.max_full_text_chars,
        )
    except Exception as exc:
        log.warning("enrich_items_failed", query=query, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] enrich_items failed: {query} -- {exc}\n")
        enriched = []
    stats.full_text_items = sum(1 for item in enriched if item.full_text)
    return stats


async def run_crawler_mode_for_target(target: str, config: AppConfig) -> CrawlerStats:
    """Build cache for one target using MiniMax + crawl4ai only."""
    dims = [d for d in config.dimensions if d.enabled]
    queries: list[tuple[Dimension, str]] = []
    for dim in dims:
        queries.extend((dim, q.replace("{target}", target)) for q in dim.minimax_queries)

    if not queries:
        return CrawlerStats(targets=1)

    # Pre-load L1 cache: one DB call to check all query hashes
    query_texts = [q for _, q in queries]
    params = search_cache_params(max_results=config.max_results_per_query)
    cached_hashes = await SearchCacheRepo().get_cached_query_hashes(query_texts, params=params)

    stats = CrawlerStats(targets=1)
    semaphore = asyncio.Semaphore(config.query_concurrency_per_dimension)

    async def run_one(dim: Dimension, query: str) -> CrawlerStats:
        async with semaphore:
            return await _crawl_query(query, dim, target, config, cached_hashes)

    results = await asyncio.gather(*(run_one(dim, query) for dim, query in queries), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.warning("crawl_query_failed", error=str(result))
            continue
        stats.add(cast(CrawlerStats, result))
    return stats


async def run_crawler_mode(
    target: str,
    config: AppConfig,
    only: list[str] | None,
    skip: list[str] | None,
) -> int:
    """CLI entry for single-target crawler mode."""
    if not settings.cache_enabled:
        sys.stderr.write("error: --crawler-mode requires CACHE_ENABLED=true\n")
        return 1
    dims, err = active_dimensions(config, only, skip)
    if err:
        sys.stderr.write(f"{err}\n")
        return 1
    config = config.model_copy(update={"dimensions": dims})

    sys.stderr.write(f"crawler mode target: {target}\n")
    sys.stderr.write(f"active dimensions: {len(dims)}\n")
    sys.stderr.write("--\n")
    stats = await run_crawler_mode_for_target(target, config)
    sys.stderr.write(
        "crawler complete: "
        f"queries={stats.queries_total}, l1_hit={stats.l1_hits}, l1_miss={stats.l1_misses}, "
        f"search_failed={stats.search_failed}, urls={stats.urls_considered}, full_text={stats.full_text_items}\n"
    )
    return 0 if stats.search_failed == 0 else 1
