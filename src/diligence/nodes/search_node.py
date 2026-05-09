"""search_node: run mmx search for all queries in one dimension."""

from __future__ import annotations

import asyncio
import sys

import structlog

from diligence.config import Dimension
from diligence.models import DimensionSearchResult
from diligence.state import DiligenceState
from diligence.utils.mmx import dedup_items, run_mmx_search

log = structlog.get_logger(__name__)


async def search_node(state: DiligenceState) -> dict:
    """Run mmx search for every query in the current dimension, dedup, and return results."""
    dim: Dimension = state["current_dimension"]
    target: str = state["target"]
    config = state["config"]
    timeout = config.search_timeout_seconds
    semaphore = asyncio.Semaphore(config.query_concurrency_per_dimension)
    max_results = config.max_results_per_query

    queries = [q.replace("{target}", target) for q in dim.search_queries]
    all_items = []
    failed_queries = 0

    async def fetch_one(query: str) -> None:
        nonlocal failed_queries
        async with semaphore:
            try:
                items = await run_mmx_search(
                    query=query,
                    dimension_id=dim.id,
                    timeout=timeout,
                    max_results=max_results,
                )
                all_items.extend(items)
            except TimeoutError:
                failed_queries += 1
                log.warning("search_timeout", dimension=dim.id, query=query)
                sys.stderr.write(f"  [{dim.name}] search timeout: {query}\n")
            except (OSError, ValueError) as exc:
                failed_queries += 1
                log.warning("search_error", dimension=dim.id, query=query, error=str(exc))
                sys.stderr.write(f"  [{dim.name}] search error: {query} -- {exc}\n")

    await asyncio.gather(*[fetch_one(q) for q in queries])

    deduped = dedup_items(all_items)
    total_queries = len(queries)

    if failed_queries == 0:
        status = "success"
    elif failed_queries < total_queries:
        status = "partial"
    else:
        status = "failed"

    dsr = DimensionSearchResult(
        dimension_id=dim.id,
        dimension_name=dim.name,
        status=status,
        items=deduped,
        error=f"{failed_queries}/{total_queries} queries failed" if failed_queries > 0 else None,
    )
    log.info("search_complete", dimension=dim.id, items=len(deduped), status=status)
    sys.stderr.write(f"  [{dim.name}] {len(deduped)} results, status={status}\n")
    return {"search_results_by_dimension": {dim.id: dsr}}
