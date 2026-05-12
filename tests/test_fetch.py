"""Tests for fetch.py: _should_fetch and enrich_items (pure logic, no crawl4ai)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from diligence.models import SearchItem, make_item_id
from diligence.utils.fetch import _crawl_priority_key, _should_fetch, enrich_items


def _make_item(
    url: str | None,
    title: str = "佛山市固特家居制品有限公司 - 企查查",
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


TARGET = "佛山市固特家居制品有限公司"


def _sf(
    url: str | None, title: str, snippet: str = "s",
    target: str = TARGET, blocked: list[str] | None = None,
) -> bool:
    """Shortcut for _should_fetch with defaults."""
    if blocked is None:
        blocked = []
    return _should_fetch(url, title, snippet, target, blocked)


# ── _should_fetch ─────────────────────────────────────────────────────────────


def test_should_fetch_normal_url_empty_blocklist() -> None:
    assert _sf("https://www.example.com/page", TARGET) is True


def test_should_fetch_blocked_domain_skipped() -> None:
    assert _sf("https://www.qixin.com/company/123", TARGET, blocked=["qixin.com"]) is False


def test_should_fetch_unblocked_domain_allowed() -> None:
    assert _sf("https://www.example.com/page", TARGET, blocked=["qixin.com"]) is True


def test_should_fetch_none_url_returns_false() -> None:
    assert _sf(None, TARGET) is False


def test_should_fetch_metaso_url_always_skipped() -> None:
    assert _sf("metaso://search?q=test", TARGET) is False


def test_should_fetch_title_mismatch_snippet_match_allowed() -> None:
    """Snippet contains target even though title doesn't — should fetch."""
    assert _sf("https://example.com/page", "广东欧享家居制品有限公司", snippet=f"该公司股东{TARGET}发生变更") is True


def test_should_fetch_both_mismatch_skipped() -> None:
    """Neither title nor snippet contains target — skip."""
    assert _sf("https://example.com/page", "无关标题", snippet="无关摘要") is False


def test_should_fetch_title_contains_target_allowed() -> None:
    assert _sf(
        "https://example.com/page",
        "佛山市固特家居制品有限公司-公司地址-企业联系方式",
    ) is True


def test_should_fetch_empty_target_disables_filter() -> None:
    assert _sf("https://example.com/page", "任何不相干的标题", target="") is True


def test_should_fetch_matches_any_blocked_fragment() -> None:
    blocked = ["qixin.com", "tianyancha.com"]
    assert _sf("https://qixin.com/company/123", TARGET, blocked=blocked) is False
    assert _sf("https://tianyancha.com/company/456", TARGET, blocked=blocked) is False
    assert _sf("https://example.com/foo", TARGET, blocked=blocked) is True


def test_should_fetch_partial_domain_substring_match() -> None:
    assert _sf("https://www.qixin.com/page", TARGET, blocked=["qixin"]) is False


# ── enrich_items — no fetch cases ─────────────────────────────────────────────


async def test_enrich_items_empty_blocklist_collects_all() -> None:
    """When blocklist is empty and titles match, eligible items are fetched.

    Commercial registry sites (qcc.com etc.) are skipped by source_registry
    should_fetch_bias=avoid regardless of blocklist.
    """
    items = [
        _make_item("https://example.com/1", title="佛山市固特家居制品有限公司 - 官网"),
        _make_item("https://example.com/2", title="佛山市固特家居制品有限公司 - 招聘"),
    ]

    fetch_count = 0

    async def fake_fetch(url: str, crawler: object, timeout_ms: int = 25000, max_chars: int = 6900) -> str:
        nonlocal fetch_count
        fetch_count += 1
        return "full page content " * 50

    mock_crawler = MagicMock()

    with patch("diligence.utils.fetch._fetch_page_markdown", new=AsyncMock(side_effect=fake_fetch)):
        result = await enrich_items(items, blocked_domains=[], target=TARGET, crawler=mock_crawler)

    assert fetch_count == 2
    assert all(item.full_text != "" for item in result)


async def test_enrich_items_both_mismatch_skipped() -> None:
    """Items whose title AND snippet both lack the target are not fetched."""
    items = [_make_item("https://qcc.com/1", title="广东欧享家居制品有限公司", snippet="s")]
    result = await enrich_items(items, blocked_domains=[], target=TARGET)
    assert result == items
    assert result[0].full_text == ""


async def test_enrich_items_snippet_match_fetched() -> None:
    """Items whose snippet (but not title) contains the target are fetched."""
    items = [
        _make_item(
            "https://example.com/page",
            title="2024广东家居行业领袖年会",
            snippet=f"{TARGET}董事长欧泽雄",
        )
    ]
    fetch_count = 0

    async def fake_fetch(url: str, crawler: object, timeout_ms: int = 25000, max_chars: int = 6900) -> str:
        nonlocal fetch_count
        fetch_count += 1
        return "full page content " * 50

    with patch("diligence.utils.fetch._fetch_page_markdown", new=AsyncMock(side_effect=fake_fetch)):
        result = await enrich_items(items, blocked_domains=[], target=TARGET)

    assert fetch_count == 1
    assert result[0].full_text != ""


async def test_enrich_items_blocked_domain_skipped() -> None:
    """Items whose URLs match a blocked domain are skipped."""
    items = [_make_item("https://qixin.com/1")]
    result = await enrich_items(items, blocked_domains=["qixin.com"], target=TARGET)
    assert result == items


async def test_enrich_items_none_url_items_skipped() -> None:
    """Items with url=None are not fetched and are returned unchanged."""
    items = [_make_item(None)]
    result = await enrich_items(items, blocked_domains=[], target=TARGET)
    assert result == items
    assert result[0].full_text == ""


async def test_enrich_items_metaso_url_skipped() -> None:
    """metaso:// URLs are never fetched (they already have full_text)."""
    items = [_make_item("metaso://search?q=test")]
    result = await enrich_items(items, blocked_domains=[], target=TARGET)
    assert result == items


async def test_enrich_items_preserves_order() -> None:
    """Item ordering is preserved regardless of which items are fetched."""
    items = [
        _make_item("https://qcc.com/1", title=TARGET + " A"),
        _make_item("https://qcc.com/2", title=TARGET + " B"),
        _make_item("https://other.com/3", title=TARGET + " C"),
    ]
    result = await enrich_items(items, blocked_domains=[], target=TARGET)
    assert [i.title for i in result] == [TARGET + " A", TARGET + " B", TARGET + " C"]


async def test_enrich_items_deduplicates_same_url() -> None:
    """Two items sharing the same URL only trigger one fetch."""
    shared_url = "https://example.com/page"
    items = [
        _make_item(shared_url, title=TARGET + " A"),
        _make_item(shared_url, title=TARGET + " B"),
    ]

    fetch_call_count = 0

    async def fake_fetch(url: str, crawler: object, timeout_ms: int = 25000, max_chars: int = 6900) -> str:
        nonlocal fetch_call_count
        fetch_call_count += 1
        return "full page content " * 50  # >100 chars to pass the short-response guard

    mock_crawler = MagicMock()

    with patch("diligence.utils.fetch._fetch_page_markdown", new=AsyncMock(side_effect=fake_fetch)):
        result = await enrich_items(items, blocked_domains=[], target=TARGET, crawler=mock_crawler)

    assert fetch_call_count == 1
    for item in result:
        assert item.full_text != ""


# ── crawl priority sorting ────────────────────────────────────────────────────


def test_crawl_priority_key_prefer_before_neutral() -> None:
    """prefer items sort before neutral items."""
    prefer = _make_item("https://gsxt.gov.cn/1", title=TARGET)
    neutral = _make_item("https://example.com/2", title=TARGET)
    key_a = _crawl_priority_key(prefer)
    key_b = _crawl_priority_key(neutral)
    assert key_a < key_b


def test_crawl_priority_key_authority_breaks_tie() -> None:
    """Items with same fetch bias are ordered by authority_level."""
    high = _make_item("https://gsxt.gov.cn/1", title=TARGET)
    low = _make_item("https://zhipin.com/job/123", title=TARGET)
    key_a = _crawl_priority_key(high)
    key_b = _crawl_priority_key(low)
    assert key_a < key_b


def test_crawl_priority_key_same_priority_equal_keys() -> None:
    """Items with identical bias and authority produce equal base keys."""
    a = _make_item("https://example.com/a", title=TARGET)
    b = _make_item("https://example.com/b", title=TARGET)
    base_a = _crawl_priority_key(a)[:2]
    base_b = _crawl_priority_key(b)[:2]
    assert base_a == base_b


async def test_enrich_items_returns_original_order_after_priority_crawl() -> None:
    """Output order matches input order even when crawl order is re-prioritised."""
    items = [
        _make_item("https://example.com/2", title=TARGET + " Second"),  # unknown bias
        _make_item("https://gsxt.gov.cn/1", title=TARGET + " First"),   # prefer bias
        _make_item("https://neutral.com/3", title=TARGET + " Third"),   # unknown bias
    ]
    fetch_urls: list[str] = []

    async def record_fetch(url: str, crawler: object, timeout_ms: int = 25000, max_chars: int = 6900) -> str:
        fetch_urls.append(url)
        return "content " * 50

    with patch("diligence.utils.fetch._fetch_page_markdown", new=AsyncMock(side_effect=record_fetch)):
        result = await enrich_items(items, blocked_domains=[], target=TARGET)

    # Output order preserved
    assert [i.title for i in result] == [TARGET + " Second", TARGET + " First", TARGET + " Third"]
    # Crawl order: gsxt.gov.cn (prefer) fetched first
    assert fetch_urls[0] == "https://gsxt.gov.cn/1"


async def test_enrich_items_avoid_items_not_fetched_but_preserved() -> None:
    """Avoid-bias items skip crawl but remain in the output for snippet fallback."""
    items = [
        _make_item("https://example.com/1", title=TARGET + " A"),       # neutral → fetched
        _make_item("https://qcc.com/2", title=TARGET + " B"),           # avoid → skipped
        _make_item("https://example.com/3", title=TARGET + " C"),       # neutral → fetched
    ]
    fetch_urls: list[str] = []

    async def record_fetch(url: str, crawler: object, timeout_ms: int = 25000, max_chars: int = 6900) -> str:
        fetch_urls.append(url)
        return "content " * 50

    with patch("diligence.utils.fetch._fetch_page_markdown", new=AsyncMock(side_effect=record_fetch)):
        result = await enrich_items(items, blocked_domains=[], target=TARGET)

    assert len(result) == 3  # all items preserved
    assert result[1].full_text == ""  # qcc.com avoid: not fetched
    assert result[0].full_text != ""  # fetched
    assert result[2].full_text != ""  # fetched
    assert "qcc.com" not in fetch_urls
