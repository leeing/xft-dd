"""crawl4ai-based page fetcher for enriching search results with full page text.

Uses crawl4ai (AsyncWebCrawler) to fetch pages and extract clean markdown content.
Handles JS rendering, bot detection, and content extraction automatically.

To enable fetching for a dimension:
1. Set fetch_enabled: true on the dimension in config.yaml
2. Blocked domains (login-walled sites) are configured in fetch_blocked_domains
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from crawl4ai import AsyncWebCrawler

from diligence.models import SearchItem
from diligence.utils.source_registry import classify_source

log = structlog.get_logger(__name__)

_METASO_SCHEME = "metaso://"
_SHORT_CRAWL_THRESHOLD = 100

_FETCH_BIAS_RANK: dict[str, int] = {"prefer": 0, "neutral": 1, "unknown": 2, "avoid": 3}
_AUTHORITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def _crawl_priority_key(item: SearchItem) -> tuple[int, int]:
    """Sort key: lower is higher priority for crawl.

    Priority order: should_fetch_bias (prefer > neutral > unknown > avoid),
    then authority_level (high > medium > low > unknown).
    The caller appends original_index as the final tiebreaker.
    """
    src = classify_source(item.url, item.title)
    return (
        _FETCH_BIAS_RANK.get(src.should_fetch_bias, 3),
        _AUTHORITY_RANK.get(src.authority_level, 3),
    )


def _should_fetch(  # noqa: PLR0913
    url: str | None,
    title: str,
    snippet: str,
    target: str,
    blocked_domains: list[str],
    full_text: str = "",
) -> bool:
    """Return True if this item should be fetched via crawl4ai.

    Always skip: None URLs, metaso:// URLs (already have full_text from API),
    items whose title AND snippet both lack the target company name.
    Uses source_registry should_fetch_bias for domain-level strategy:
    - "avoid" pages are skipped: not fetched, but the original item (snippet/metaso content) is preserved.
    - "avoid" pages that already have full_text are not re-fetched.
    """
    if not url or url.startswith(_METASO_SCHEME):
        return False
    if target not in title and target not in snippet:
        return False

    source = classify_source(url, title)
    if source.should_fetch_bias == "avoid":
        if full_text:
            return False  # already have content, skip re-fetch
        return False  # commercial registry etc. — skip

    if not blocked_domains:
        return True
    return not any(domain in url for domain in blocked_domains)


async def _fetch_page_markdown(
    url: str,
    crawler: AsyncWebCrawler,
    *,
    timeout_ms: int = 25000,
    max_chars: int = 6900,
) -> str:
    """Fetch URL via crawl4ai and return extracted markdown content."""
    try:
        result = await asyncio.wait_for(
            crawler.arun(url=url),
            timeout=timeout_ms / 1000,
        )
        if result and result.success and result.markdown:
            text = result.markdown
            if len(text) < _SHORT_CRAWL_THRESHOLD:
                log.warning("crawl_short_response", url=url, chars=len(text))
                return ""
            return text[:max_chars]
        if result and not result.success:
            log.warning("crawl_failed", url=url, error=result.error_message)
            return ""
        else:
            return ""
    except TimeoutError:
        log.warning("crawl_timeout", url=url)
        return ""
    except (OSError, ValueError) as exc:
        log.warning("crawl_error", url=url, error=str(exc))
        return ""


async def enrich_items(  # noqa: PLR0913
    items: list[SearchItem],
    blocked_domains: list[str],
    *,
    target: str = "",
    fetch_timeout: int = 25,
    concurrency: int = 2,
    max_full_text_chars: int = 6900,
    crawler: AsyncWebCrawler | None = None,
) -> list[SearchItem]:
    """crawl4ai-fetch items not matching any blocked domain.

    Args:
        items: Search results to enrich.
        blocked_domains: Domain fragments to skip (e.g. ["qixin.com"]).
            Items whose URL contains any of these fragments are NOT fetched.
            When empty, all eligible (non-metaso) URLs are fetched.
        target: Company name. Only items whose title or snippet contain the target are fetched.
            Empty string disables the filter.
        fetch_timeout: Per-page fetch timeout in seconds.
        concurrency: Max concurrent crawl4ai fetches.
        max_full_text_chars: Max chars to retain per fetched page.
        crawler: Optional AsyncWebCrawler instance. If omitted, a temporary one is created.

    Returns enriched item list. Metaso URLs, blocked-domain URLs, and
    title-mismatched items keep their original snippet. All other URLs are fetched.
    """
    work: list[tuple[int, SearchItem, str]] = []  # (original_index, item, url)
    seen: set[str] = set()
    for i, item in enumerate(items):
        ok = _should_fetch(item.url, item.title, item.snippet, target, blocked_domains, item.full_text)
        if ok and item.url not in seen:
            work.append((i, item, item.url))
            seen.add(item.url)

    if not work:
        return items

    # Sort by crawl priority so high-value sources get crawl budget first
    work.sort(key=lambda x: _crawl_priority_key(x[1]) + (x[0],))

    async def _run(crawler: AsyncWebCrawler) -> list[SearchItem]:
        semaphore = asyncio.Semaphore(concurrency)

        async def do_fetch(item: SearchItem, url: str) -> SearchItem:
            async with semaphore:
                src = classify_source(item.url, item.title)
                sys.stderr.write(f"  [fetch] {url[:80]} (bias={src.should_fetch_bias}, auth={src.authority_level})\n")
                text = await _fetch_page_markdown(
                    url,
                    crawler,
                    timeout_ms=fetch_timeout * 1000,
                    max_chars=max_full_text_chars,
                )
                if text:
                    log.debug("fetch_enriched", url=url, chars=len(text))
                    return item.model_copy(update={"full_text": text, "snippet": text[:300]})
                return item

        enriched_map: dict[str, SearchItem] = {}
        fetch_tasks = [do_fetch(item, url) for _, item, url in work]
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        for (_, item, _), result in zip(work, results, strict=False):
            if isinstance(result, SearchItem):
                enriched_map[item.id] = result
            else:
                log.warning("fetch_task_failed", item_id=item.id, error=str(result))

        return [enriched_map.get(item.id, item) for item in items]

    if crawler is not None:
        return await _run(crawler)

    async with AsyncWebCrawler() as new_crawler:
        return await _run(new_crawler)
