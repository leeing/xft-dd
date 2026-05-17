"""Fetch Web result pages with crawl4ai and persist page files."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from xft.core.search_models import SearchItem
from xft.progress import display
from xft.utils.fetch import enrich_items
from xft.web.cache_writer import WebCacheWriter
from xft.web.models import WebFetchConfig, WebPageRecord, WebSearchResultRecord


async def fetch_and_cache_pages(
    *,
    records: list[WebSearchResultRecord],
    writer: WebCacheWriter,
    web_run_id: str,
    target: str,
    config: WebFetchConfig,
) -> list[WebSearchResultRecord]:
    """Fetch page text, write pages/* files, and return records with page refs."""
    if not config.enabled or not records:
        return records
    items = [
        SearchItem(
            id=record.result_id,
            title=record.title,
            url=record.url,
            snippet=record.snippet,
            full_text=record.full_text,
            query="",
            dimension_id=record.dimension_id,
            source=record.source,  # type: ignore[arg-type]
            rank=record.rank,
            fetched_at=record.created_at,
        )
        for record in records
    ]
    enriched = await enrich_items(
        items,
        blocked_domains=config.blocked_domains,
        target=target,
        fetch_timeout=config.timeout_seconds,
        concurrency=config.concurrency,
        max_full_text_chars=config.max_full_text_chars,
    )
    fetched_count = sum(1 for item in enriched if item.full_text)
    skipped_count = len(enriched) - fetched_count
    display.info(f"📄 爬取页面: {len(records)}个URL, {fetched_count}个成功, {skipped_count}个跳过")
    by_id = {item.id: item for item in enriched}
    updated: list[WebSearchResultRecord] = []
    for record in records:
        item = by_id.get(record.result_id)
        text = item.full_text if item else record.full_text
        url_display = (record.url or "")[:80]
        if not text:
            display.branch(f"⏭ {url_display} → 跳过 (无内容)")
            updated.append(record.model_copy(update={"full_text": "", "full_text_preview": ""}))
            continue
        display.branch(f"✓ {url_display} → {len(text)}字符")
        content_hash = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()
        page_path, metadata_path = writer.write_page(
            content_hash,
            text,
            {
                "result_id": record.result_id,
                "url": record.url,
                "title": record.title,
                "text_length": len(text),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        writer.append_page(
            WebPageRecord(
                page_id=f"p_{content_hash[:12]}",
                web_run_id=web_run_id,
                result_id=record.result_id,
                url=record.url,
                title=record.title,
                content_hash=content_hash,
                page_path=page_path,
                metadata_path=metadata_path,
                text_length=len(text),
                status="success",
                created_at=datetime.now(UTC),
            )
        )
        updated.append(
            record.model_copy(
                update={
                    "full_text": "",
                    "full_text_preview": text[:500],
                    "content_hash": content_hash,
                    "page_path": page_path,
                    "snippet": text[:300] if text else record.snippet,
                }
            )
        )
    return updated
