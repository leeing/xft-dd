"""Tests for fetch.py: _is_fetchable and enrich_items (pure logic, no Playwright)."""

from __future__ import annotations

from datetime import UTC, datetime

from diligence.models import SearchItem, make_item_id
from diligence.utils.fetch import _is_fetchable, enrich_items


def _make_item(
    url: str | None,
    title: str = "t",
    snippet: str = "s",
    dim_id: str = "basic_info",
) -> SearchItem:
    return SearchItem(
        id=make_item_id(url=url, title=title, snippet=snippet),
        title=title,
        url=url,
        snippet=snippet,
        query="q",
        dimension_id=dim_id,
        fetched_at=datetime.now(UTC),
    )


# ── _is_fetchable ─────────────────────────────────────────────────────────────


def test_is_fetchable_matches_domain_fragment() -> None:
    assert _is_fetchable("https://www.example.com/page", ["example.com"]) is True


def test_is_fetchable_no_match() -> None:
    assert _is_fetchable("https://www.other.com/page", ["example.com"]) is False


def test_is_fetchable_none_url_returns_false() -> None:
    assert _is_fetchable(None, ["example.com"]) is False


def test_is_fetchable_empty_domains_returns_false() -> None:
    assert _is_fetchable("https://example.com", []) is False


def test_is_fetchable_matches_any_fragment_in_list() -> None:
    domains = ["qcc.com", "example.com"]
    assert _is_fetchable("https://qcc.com/company/123", domains) is True
    assert _is_fetchable("https://example.com/foo", domains) is True


def test_is_fetchable_partial_domain_substring_match() -> None:
    """Matching is substring-based, so 'example' matches 'example.com'."""
    assert _is_fetchable("https://www.example.com/page", ["example"]) is True


# ── enrich_items — no fetch cases ─────────────────────────────────────────────


async def test_enrich_items_empty_domains_no_op() -> None:
    """When fetchable_domains is empty, items are returned unchanged."""
    items = [_make_item("https://qcc.com/1"), _make_item("https://tianyancha.com/1")]
    result = await enrich_items(items, fetchable_domains=[])
    assert result == items


async def test_enrich_items_no_matching_url_no_op() -> None:
    """Items whose URLs don't match any domain are returned unchanged."""
    items = [_make_item("https://qcc.com/1")]
    result = await enrich_items(items, fetchable_domains=["example.com"])
    assert result == items


async def test_enrich_items_none_url_items_skipped() -> None:
    """Items with url=None are not fetched and are returned unchanged."""
    items = [_make_item(None)]
    result = await enrich_items(items, fetchable_domains=["example.com"])
    assert result == items
    assert result[0].full_text == ""


async def test_enrich_items_preserves_order() -> None:
    """Item ordering is preserved regardless of which items are fetched."""
    items = [
        _make_item("https://qcc.com/1", title="A"),
        _make_item("https://qcc.com/2", title="B"),
        _make_item("https://other.com/3", title="C"),
    ]
    result = await enrich_items(items, fetchable_domains=["nonexistent.com"])
    assert [i.title for i in result] == ["A", "B", "C"]


async def test_enrich_items_deduplicates_same_url() -> None:
    """Two items sharing the same URL only trigger one fetch."""
    from unittest.mock import AsyncMock, patch

    shared_url = "https://example.com/page"
    items = [
        _make_item(shared_url, title="A"),
        _make_item(shared_url, title="B"),
    ]

    fetch_call_count = 0

    async def fake_fetch(url: str, timeout_ms: int = 15000) -> str:
        nonlocal fetch_call_count
        fetch_call_count += 1
        return "full page content " * 50  # >500 chars to pass the short-response guard

    with patch("diligence.utils.fetch._fetch_page_text", new=AsyncMock(side_effect=fake_fetch)):
        result = await enrich_items(items, fetchable_domains=["example.com"])

    # Only one fetch despite two items sharing the URL
    assert fetch_call_count == 1
    # Both items should be enriched with the fetched text
    for item in result:
        assert item.full_text != ""
