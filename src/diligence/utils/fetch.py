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

log = structlog.get_logger(__name__)

_METASO_SCHEME = "metaso://"


def _should_fetch(
    url: str | None, title: str, snippet: str, target: str, blocked_domains: list[str],
) -> bool:
    """Return True if this item should be fetched via crawl4ai.

    Always skip: None URLs, metaso:// URLs (already have full_text from API),
    items whose title AND snippet both lack the target company name.
    Blacklist: URLs containing any blocked domain fragment are skipped.
    When blocklist is empty: all eligible URLs are fetched.
    """
    if not url or url.startswith(_METASO_SCHEME):
        return False
    if target not in title and target not in snippet:
        return False
    if not blocked_domains:
        return True
    return not any(domain in url for domain in blocked_domains)


async def _fetch_page_markdown(
    url: str, crawler: AsyncWebCrawler, *, timeout_ms: int = 25000, max_chars: int = 6900,
) -> str:
    """Fetch URL via crawl4ai and return extracted markdown content."""
    try:
        result = await asyncio.wait_for(
            crawler.arun(url=url),
            timeout=timeout_ms / 1000,
        )
        if result and result.success and result.markdown:
            text = result.markdown
            if len(text) < 100:  # suspiciously short — likely a blocker page
                log.warning("crawl_short_response", url=url, chars=len(text))
                return ""
            return text[:max_chars]
        if result and not result.success:
            log.warning("crawl_failed", url=url, error=result.error_message)
        return ""
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("crawl_timeout", url=url)
        return ""
    except (OSError, ValueError) as exc:
        log.warning("crawl_error", url=url, error=str(exc))
        return ""


async def enrich_items(
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
    work: list[tuple[SearchItem, str]] = []
    seen: set[str] = set()
    for item in items:
        if _should_fetch(item.url, item.title, item.snippet, target, blocked_domains) and item.url not in seen:
            work.append((item, item.url))
            seen.add(item.url)

    if not work:
        return items

    async def _run(crawler: AsyncWebCrawler) -> list[SearchItem]:
        semaphore = asyncio.Semaphore(concurrency)

        async def do_fetch(item: SearchItem, url: str) -> SearchItem:
            async with semaphore:
                sys.stderr.write(f"  [fetch] {url[:80]}\n")
                text = await _fetch_page_markdown(url, crawler, timeout_ms=fetch_timeout * 1000, max_chars=max_full_text_chars)
                if text:
                    log.debug("fetch_enriched", url=url, chars=len(text))
                    return item.model_copy(update={"full_text": text, "snippet": text[:300]})
                return item

        enriched_map: dict[str, SearchItem] = {}
        fetch_tasks = [do_fetch(item, url) for item, url in work]
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        for (item, _), result in zip(work, results, strict=False):
            if isinstance(result, SearchItem):
                enriched_map[item.id] = result
            else:
                log.warning("fetch_task_failed", item_id=item.id, error=str(result))

        return [enriched_map.get(item.id, item) for item in items]

    if crawler is not None:
        return await _run(crawler)

    async with AsyncWebCrawler() as new_crawler:
        return await _run(new_crawler)
