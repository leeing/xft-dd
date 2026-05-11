"""Playwright-based page fetcher for enriching search results with full page text.

Currently no domains are reliably fetchable without login in automated mode:

Verified NOT working (blocked, login-walled, or bot-detection):
  - www.qixin.com       → login required for all pages (session-only access)
  - www.tianyancha.com  → login wall even in headed mode
  - www.gsxt.gov.cn     → Cloudflare 521 + IP blacklist
  - aiqicha.baidu.com   → baidu slider captcha
  - www.innocom.gov.cn  → connection timeout
  - pss-system.cponline.cnipa.gov.cn → 412

This module is kept as a framework for future domain additions when new
publicly-accessible sources are identified. Set fetch_enabled: false in
config.yaml dimensions to skip enrichment.

To re-enable fetching for a new domain:
1. Add the domain fragment to fetchable_domains list in config.yaml
2. Set fetch_enabled: true on the relevant dimension(s)
3. Test with the headed browser: headed mode bypasses basic bot detection
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from diligence.models import SearchItem

log = structlog.get_logger(__name__)

# How many chars to keep from fetched full text (avoid bloating the prompt)
MAX_FULL_TEXT_CHARS = 6000


class FetchSession:
    """Managed Playwright browser lifecycle for page fetching.

    Usage:
        async with FetchSession(headless=True) as session:
            enriched = await enrich_items(items, domains, session=session)
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> FetchSession:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-position=0,0",
                "--window-size=1280,900",
            ],
        )
        mode = "headless" if self._headless else "headed"
        sys.stderr.write(f"  [fetch] 浏览器已启动（{mode} 模式）\n")
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._browser and self._browser.is_connected():
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        sys.stderr.write("  [fetch] 浏览器已关闭\n")

    async def get_browser(self) -> Browser:
        """Return the managed browser instance."""
        if self._browser is None:
            msg = "FetchSession not entered"
            raise RuntimeError(msg)
        return self._browser


async def _fetch_page_text(url: str, browser: Browser, timeout_ms: int = 15000) -> str:
    """Open url in a fresh context, return visible body text (truncated)."""
    ctx: BrowserContext = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    page: Page = await ctx.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        text = await page.inner_text("body")
        if len(text) < 500:  # noqa: PLR2004 — suspicious short response (bot blocker page)
            log.warning("fetch_short_response", url=url, chars=len(text))
            return ""
        return text[:MAX_FULL_TEXT_CHARS]
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        log.warning("fetch_page_failed", url=url, error=str(exc))
        return ""
    finally:
        await ctx.close()


def _is_fetchable(url: str | None, fetchable_domains: list[str]) -> bool:
    """Return True if this item's URL contains a whitelisted domain fragment."""
    if not url:
        return False
    return any(domain in url for domain in fetchable_domains)


async def enrich_items(
    items: list[SearchItem],
    fetchable_domains: list[str],
    *,
    fetch_timeout: int = 25,
    concurrency: int = 2,
    session: FetchSession | None = None,
) -> list[SearchItem]:
    """Playwright-fetch items whose URL matches a whitelisted domain fragment.

    Args:
        items: Search results to enrich.
        fetchable_domains: Domain fragments to whitelist (e.g. ["example.com"]).
            Any item whose URL contains one of these fragments will be fetched.
        fetch_timeout: Per-page fetch timeout in seconds.
        concurrency: Max concurrent Playwright page fetches.
        session: Optional FetchSession. If omitted, a temporary headless session is created.

    Returns enriched item list. Items without a matching domain keep their
    original snippet. No-op when fetchable_domains is empty.
    """
    work: list[tuple[SearchItem, str]] = []
    seen: set[str] = set()
    for item in items:
        if item.url and _is_fetchable(item.url, fetchable_domains) and item.url not in seen:
            work.append((item, item.url))
            seen.add(item.url)

    if not work:
        return items

    async def _run(session: FetchSession) -> list[SearchItem]:
        browser = await session.get_browser()
        semaphore = asyncio.Semaphore(concurrency)

        async def do_fetch(item: SearchItem, url: str) -> SearchItem:
            async with semaphore:
                sys.stderr.write(f"  [fetch] {url[:80]}\n")
                text = await asyncio.wait_for(
                    _fetch_page_text(url, browser, timeout_ms=fetch_timeout * 1000),
                    timeout=fetch_timeout + 5,
                )
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

    if session is not None:
        return await _run(session)

    async with FetchSession(headless=True) as temp_session:
        return await _run(temp_session)
