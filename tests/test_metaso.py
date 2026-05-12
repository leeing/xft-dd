"""Tests for metaso.py: _clean_answer, make_metaso_item, fetch_metaso_items, enrich_with_metaso."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from diligence.utils.metaso import (
    _clean_answer,
    enrich_with_metaso,
    enrich_with_metaso_search,
    fetch_metaso_items,
    fetch_metaso_search_items,
    make_metaso_item,
    make_metaso_search_item,
    make_metaso_source_items,
)


# ── _clean_answer ─────────────────────────────────────────────────────────────


def test_clean_answer_removes_thinking_lines() -> None:
    """Lines starting with '>' (thinking chain) are stripped."""
    raw = "> 思考中...\n> 继续思考\n最终答案是统一社会信用代码91440300MA5DXXXX"
    result = _clean_answer(raw)
    assert "思考中" not in result
    assert "最终答案" in result


def test_clean_answer_removes_citation_markers() -> None:
    """[[N]] citation markers are removed."""
    raw = "注册资本为1000万元人民币[[1]]，成立于2010年[[2]]。"
    result = _clean_answer(raw)
    assert "[[1]]" not in result
    assert "[[2]]" not in result
    assert "注册资本" in result


def test_clean_answer_collapses_blank_lines() -> None:
    """Three or more consecutive blank lines are collapsed to two."""
    raw = "段落一\n\n\n\n\n段落二"
    result = _clean_answer(raw)
    assert "\n\n\n" not in result


def test_clean_answer_strips_whitespace() -> None:
    """Leading and trailing whitespace is stripped."""
    raw = "  \n  某公司是一家制造业企业  \n  "
    result = _clean_answer(raw)
    assert result == "某公司是一家制造业企业"


def test_clean_answer_empty_string() -> None:
    """Empty input returns empty string without error."""
    assert _clean_answer("") == ""


def test_clean_answer_pure_thinking_returns_empty() -> None:
    """All-thinking input collapses to empty string."""
    raw = "> line1\n> line2\n> line3"
    result = _clean_answer(raw)
    assert result == ""


# ── make_metaso_item ──────────────────────────────────────────────────────────


def test_make_metaso_item_fields() -> None:
    """SearchItem returned has expected field values."""
    item = make_metaso_item("公司成立于2010年", "某公司的成立时间", "basic_info")
    assert item.dimension_id == "basic_info"
    assert item.full_text == "公司成立于2010年"
    assert item.rank == 0
    assert item.source == "metaso_chat"
    assert item.title == "某公司的成立时间"  # query as title, no prefix
    assert item.url is not None and item.url.startswith("metaso://")


def test_make_metaso_item_snippet_truncated() -> None:
    """snippet is at most 300 chars of the answer."""
    long_answer = "A" * 500
    item = make_metaso_item(long_answer, "query", "basic_info")
    assert len(item.snippet) <= 300


def test_make_metaso_item_long_query_truncated() -> None:
    """Query longer than 80 chars is truncated in the title."""
    long_query = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的查询词" * 3
    item = make_metaso_item("answer", long_query, "basic_info")
    assert len(item.title) <= 80


# ── fetch_metaso_items ────────────────────────────────────────────────────────


async def test_fetch_metaso_items_returns_empty_when_no_api_key() -> None:
    """Empty api_key short-circuits immediately, no HTTP calls."""
    items, source_items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="")
    assert items == []
    assert source_items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_items_returns_empty_when_no_queries() -> None:
    """Empty query list short-circuits immediately."""
    items, source_items, success, failed, credits = await fetch_metaso_items("basic_info", [], api_key="key")
    assert items == []
    assert source_items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_items_success() -> None:
    """Returns SearchItems and sums credits on success."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("公司注册资本为人民币壹仟万元整，经营状态正常", [], 3)
        items, source_items, success, failed, credits = await fetch_metaso_items(
            "basic_info", ["某公司的注册资本是多少？"], api_key="key"
        )
    assert len(items) == 1
    assert success == 1
    assert failed == 0
    assert credits == 3
    assert items[0].full_text == "公司注册资本为人民币壹仟万元整，经营状态正常"


async def test_fetch_metaso_items_skips_short_answer() -> None:
    """Answers shorter than 20 chars are discarded (likely a non-answer)."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("无", [], 1)  # too short
        items, source_items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 1  # credits still counted even for short answers


async def test_fetch_metaso_items_handles_timeout_gracefully() -> None:
    """Timeout exception is caught; returns empty list, zero credits."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = TimeoutError("timeout")
        items, source_items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert source_items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_items_handles_http_error_gracefully() -> None:
    """HTTP error is caught; returns empty list."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        items, source_items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_items_multiple_queries_summed() -> None:
    """Credits from multiple queries are summed."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = [
            ("公司成立于2010年，注册资本为人民币壹仟万元整，经营状态正常", [], 2),
            ("法定代表人为张三，经营范围包括家具制造与销售，注册地址广东省佛山市", [], 3),
        ]
        items, source_items, success, failed, credits = await fetch_metaso_items(
            "basic_info", ["query1", "query2"], api_key="key"
        )
    assert len(items) == 2
    assert success == 2
    assert failed == 0
    assert credits == 5


# ── enrich_with_metaso ────────────────────────────────────────────────────────


async def test_enrich_with_metaso_prepends_items() -> None:
    """Metaso items are prepended before existing search result items."""
    from datetime import UTC, datetime

    from diligence.models import SearchItem, make_item_id

    existing = SearchItem(
        id=make_item_id(url="https://qcc.com/1", title="企查查结果", snippet="s"),
        title="企查查结果",
        url="https://qcc.com/1",
        snippet="s",
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
    )

    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("秘塔AI的答案内容，超过20个字符的有效答案", [], 2)
        enriched, success, failed, credits = await enrich_with_metaso(
            items=[existing],
            dimension_id="basic_info",
            queries=["某公司注册资本"],
            api_key="key",
        )

    assert len(enriched) == 2
    assert success == 1
    assert failed == 0
    # Metaso item comes first (rank=0)
    assert enriched[0].rank == 0
    assert enriched[0].full_text != ""
    # Original item preserved at the end
    assert enriched[-1].id == existing.id
    assert credits == 2


async def test_enrich_with_metaso_no_key_returns_original() -> None:
    """Empty API key → original items returned unchanged, zero credits."""
    from datetime import UTC, datetime

    from diligence.models import SearchItem, make_item_id

    existing = SearchItem(
        id=make_item_id(url="https://qcc.com/1", title="t", snippet="s"),
        title="t",
        url="https://qcc.com/1",
        snippet="s",
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
    )
    enriched, success, failed, credits = await enrich_with_metaso(
        items=[existing], dimension_id="basic_info", queries=["q"], api_key=""
    )
    assert enriched == [existing]
    assert success == 0
    assert failed == 0
    assert credits == 0


# ── make_metaso_source_items ──────────────────────────────────────────────────


def test_make_metaso_source_items_converts_sources() -> None:
    """Each source with a link becomes a SearchItem with real URL."""
    sources = [
        {"title": "企查查 - 某公司", "link": "https://www.qcc.com/company/123", "summary": "AI摘要内容"},
        {"title": "招聘页面", "link": "https://www.job5156.com/gongsi/abc", "snippet": "公司名称：某公司"},
    ]
    items = make_metaso_source_items(sources, "某公司 工商信息", "basic_info")
    assert len(items) == 2
    assert items[0].url == "https://www.qcc.com/company/123"
    assert items[0].full_text == ""
    assert items[0].source == "metaso_chat"
    assert items[0].rank == 0
    assert items[1].url == "https://www.job5156.com/gongsi/abc"
    assert items[1].rank == 1
    assert items[1].snippet == "公司名称：某公司"


def test_make_metaso_source_items_skips_empty_links() -> None:
    """Sources with no link field or empty link are filtered out."""
    sources = [
        {"title": "无链接", "link": "", "summary": "s"},
        {"title": "正常", "link": "https://example.com/page", "summary": "ok"},
    ]
    items = make_metaso_source_items(sources, "query", "basic_info")
    assert len(items) == 1
    assert items[0].url == "https://example.com/page"


def test_make_metaso_source_items_empty_sources() -> None:
    """Empty sources list returns empty items list."""
    assert make_metaso_source_items([], "query", "basic_info") == []


def test_make_metaso_source_items_prefers_summary_over_snippet() -> None:
    """When both summary and snippet exist, summary is used as snippet."""
    sources = [
        {
            "title": "t",
            "link": "https://example.com/page",
            "summary": "AI摘要",
            "snippet": "搜索片段",
        }
    ]
    items = make_metaso_source_items(sources, "query", "basic_info")
    assert items[0].snippet == "AI摘要"


# ── fetch_metaso_items with source_items integration ──────────────────────────


async def test_fetch_metaso_items_includes_source_items() -> None:
    """Source items from all queries are collected alongside answer items."""
    mock_sources = [
        {"title": "来源1", "link": "https://example.com/1", "summary": "摘要1"},
        {"title": "来源2", "link": "https://example.com/2", "snippet": "片段2"},
    ]
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("企业成立于2010年，注册资本为人民壹仟万元整，经营状态正常", mock_sources, 6)
        answer_items, source_items, success, failed, credits = await fetch_metaso_items(
            "basic_info", ["某公司信息"], api_key="key"
        )
    assert len(answer_items) == 1
    assert answer_items[0].full_text != ""
    assert len(source_items) == 2
    assert source_items[0].url == "https://example.com/1"
    assert source_items[1].url == "https://example.com/2"
    assert all(it.full_text == "" for it in source_items)
    assert success == 1
    assert credits == 6


async def test_fetch_metaso_items_source_items_on_short_answer() -> None:
    """Even when answer is too short, source items are still returned."""
    mock_sources = [
        {"title": "来源1", "link": "https://example.com/1", "summary": "摘要1"},
    ]
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("太短", mock_sources, 1)
        answer_items, source_items, success, failed, credits = await fetch_metaso_items(
            "basic_info", ["query"], api_key="key"
        )
    assert answer_items == []
    assert len(source_items) == 1
    assert source_items[0].url == "https://example.com/1"
    assert success == 0
    assert failed == 1


async def test_enrich_with_metaso_prepends_source_items_first() -> None:
    """Source items come before answer items, which come before existing items."""
    from datetime import UTC, datetime

    from diligence.models import SearchItem, make_item_id

    existing = SearchItem(
        id=make_item_id(url="https://qcc.com/1", title="企查查结果", snippet="s"),
        title="企查查结果",
        url="https://qcc.com/1",
        snippet="s",
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
    )
    mock_sources = [
        {"title": "来源页", "link": "https://example.com/src", "summary": "源摘要"},
    ]
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("秘塔AI的答案内容，超过20个字符的有效答案", mock_sources, 2)
        enriched, success, failed, credits = await enrich_with_metaso(
            items=[existing],
            dimension_id="basic_info",
            queries=["某公司注册资本"],
            api_key="key",
        )

    assert len(enriched) == 3
    # source item first
    assert enriched[0].url == "https://example.com/src"
    assert enriched[0].full_text == ""
    assert enriched[0].source == "metaso_chat"
    # answer item second
    assert enriched[1].url is not None and enriched[1].url.startswith("metaso://")
    assert enriched[1].full_text != ""
    # existing item last
    assert enriched[2].id == existing.id
    assert success == 1
    assert credits == 2


# ── search mode: make_metaso_search_item ─────────────────────────────────────


def test_make_metaso_search_item_uses_content_as_full_text() -> None:
    """content field becomes full_text when available."""
    wp = {
        "title": "企查查 - 某公司",
        "link": "https://www.qcc.com/company/123",
        "summary": "AI摘要内容",
        "content": "原始页面正文内容",
    }
    item = make_metaso_search_item(wp, "query", "ip", rank=3)
    assert item.full_text == "原始页面正文内容"
    assert item.url == "https://www.qcc.com/company/123"
    assert item.rank == 3
    assert item.source == "metaso_search"
    assert item.title == "企查查 - 某公司"  # original title, no prefix


def test_make_metaso_search_item_falls_back_to_summary() -> None:
    """When content is empty, summary becomes full_text."""
    wp = {
        "title": "某页面",
        "link": "https://example.com/page",
        "summary": "AI生成的摘要",
        "content": "",
    }
    item = make_metaso_search_item(wp, "query", "ip", rank=0)
    assert item.full_text == "AI生成的摘要"


def test_make_metaso_search_item_falls_back_to_snippet() -> None:
    """When both content and summary are empty, snippet becomes full_text."""
    wp = {
        "title": "某页面",
        "link": "https://example.com/page",
        "summary": "",
        "content": "",
        "snippet": "搜索引擎摘要片段",
    }
    item = make_metaso_search_item(wp, "query", "ip", rank=0)
    assert item.full_text == "搜索引擎摘要片段"


def test_make_metaso_search_item_uses_summary_for_display_snippet() -> None:
    """Display snippet prefers summary (AI-generated, most informative)."""
    wp = {
        "title": "某页面",
        "link": "https://example.com/page",
        "summary": "AI摘要文本",
        "content": "正文内容很长..." * 50,
        "snippet": "搜索片段",
    }
    item = make_metaso_search_item(wp, "query", "ip", rank=0)
    assert item.snippet == "AI摘要文本"


def test_make_metaso_search_item_real_url_preserved() -> None:
    """Real http(s) URLs are preserved (unlike chat mode's metaso://)."""
    wp = {"title": "t", "link": "https://www.tianyancha.com/company/abc", "summary": "s"}
    item = make_metaso_search_item(wp, "query", "ip", rank=0)
    assert item.url == "https://www.tianyancha.com/company/abc"
    assert not item.url.startswith("metaso://")


# ── search mode: fetch_metaso_search_items ───────────────────────────────────


async def test_fetch_metaso_search_items_returns_empty_when_no_api_key() -> None:
    items, success, failed, credits = await fetch_metaso_search_items("ip", ["query"], api_key="")
    assert items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_search_items_returns_empty_when_no_queries() -> None:
    items, success, failed, credits = await fetch_metaso_search_items("ip", [], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_search_items_success() -> None:
    """Returns SearchItems with real URLs and sums credits."""
    mock_webpages = [
        {
            "title": "结果1 - 天眼查",
            "link": "https://www.tianyancha.com/company/1",
            "summary": "摘要1",
            "content": "正文内容1",
        },
        {
            "title": "结果2 - 企查查",
            "link": "https://www.qcc.com/company/2",
            "summary": "摘要2",
            "content": "",
        },
    ]
    with patch("diligence.utils.metaso.query_metaso_search", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = (mock_webpages, 12)
        items, success, failed, credits = await fetch_metaso_search_items("ip", ["某公司专利"], api_key="key", size=3)
    assert len(items) == 2
    assert success == 1
    assert failed == 0
    assert credits == 12
    assert items[0].url == "https://www.tianyancha.com/company/1"
    assert items[0].full_text == "正文内容1"
    assert items[1].full_text == "摘要2"  # fallback to summary


async def test_fetch_metaso_search_items_empty_webpages_counts_as_failed() -> None:
    """Empty webpages list from API counts as a failed query."""
    with patch("diligence.utils.metaso.query_metaso_search", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ([], 0)
        items, success, failed, credits = await fetch_metaso_search_items("ip", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_search_items_handles_timeout() -> None:
    with patch("diligence.utils.metaso.query_metaso_search", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = TimeoutError("timeout")
        items, success, failed, credits = await fetch_metaso_search_items("ip", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_search_items_interleaves_by_rank() -> None:
    """Results from multiple queries are interleaved: all rank-0, then all rank-1, etc."""
    q1_results = [
        {"title": "Q1-R0", "link": "https://a.com/1", "summary": "a"},
        {"title": "Q1-R1", "link": "https://a.com/2", "summary": "b"},
    ]
    q2_results = [
        {"title": "Q2-R0", "link": "https://b.com/1", "summary": "c"},
    ]
    with patch("diligence.utils.metaso.query_metaso_search", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = [(q1_results, 6), (q2_results, 6)]
        items, success, failed, credits = await fetch_metaso_search_items("ip", ["q1", "q2"], api_key="key", size=3)
    # Q1-R0, Q2-R0, Q1-R1 (rank 0s first, then rank 1s)
    assert [it.title for it in items] == ["Q1-R0", "Q2-R0", "Q1-R1"]
    assert success == 2
    assert credits == 12


# ── search mode: enrich_with_metaso_search ────────────────────────────────────


async def test_enrich_with_metaso_search_prepends_items() -> None:
    """Metaso search items are prepended before existing items."""
    from datetime import UTC, datetime

    from diligence.models import SearchItem, make_item_id

    existing = SearchItem(
        id=make_item_id(url="https://qcc.com/1", title="企查查结果", snippet="s"),
        title="企查查结果",
        url="https://qcc.com/1",
        snippet="s",
        query="q",
        dimension_id="ip",
        fetched_at=datetime.now(UTC),
    )

    mock_webpages = [
        {"title": "专利结果", "link": "https://patents.example.com/1", "summary": "专利摘要", "content": "专利正文"},
    ]
    with patch("diligence.utils.metaso.query_metaso_search", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = (mock_webpages, 12)
        enriched, success, failed, credits = await enrich_with_metaso_search(
            items=[existing], dimension_id="ip", queries=["某公司专利"], api_key="key"
        )

    assert len(enriched) == 2
    assert success == 1
    assert failed == 0
    assert credits == 12
    # Metaso search item comes first
    assert enriched[0].url == "https://patents.example.com/1"
    assert enriched[0].full_text == "专利正文"
    # Original item preserved at the end
    assert enriched[-1].id == existing.id


async def test_enrich_with_metaso_search_no_key_returns_original() -> None:
    """Empty API key → original items returned unchanged."""
    from datetime import UTC, datetime

    from diligence.models import SearchItem, make_item_id

    existing = SearchItem(
        id=make_item_id(url="https://qcc.com/1", title="t", snippet="s"),
        title="t",
        url="https://qcc.com/1",
        snippet="s",
        query="q",
        dimension_id="ip",
        fetched_at=datetime.now(UTC),
    )
    enriched, success, failed, credits = await enrich_with_metaso_search(
        items=[existing], dimension_id="ip", queries=["q"], api_key=""
    )
    assert enriched == [existing]
    assert success == 0
    assert failed == 0
    assert credits == 0
    assert credits == 0
