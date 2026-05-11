"""Tests for metaso.py: _clean_answer, make_metaso_item, fetch_metaso_items, enrich_with_metaso."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from diligence.utils.metaso import (
    _clean_answer,
    enrich_with_metaso,
    fetch_metaso_items,
    make_metaso_item,
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
    assert item.rank == 0  # always placed first
    assert "某公司" in item.title
    assert item.url is not None and item.url.startswith("metaso://")


def test_make_metaso_item_snippet_truncated() -> None:
    """snippet is at most 300 chars of the answer."""
    long_answer = "A" * 500
    item = make_metaso_item(long_answer, "query", "basic_info")
    assert len(item.snippet) <= 300


def test_make_metaso_item_long_query_truncated() -> None:
    """Query longer than 60 chars is truncated in the title."""
    long_query = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的查询词" * 3
    item = make_metaso_item("answer", long_query, "basic_info")
    assert len(item.title) < len(long_query) + 10  # title is trimmed


# ── fetch_metaso_items ────────────────────────────────────────────────────────


async def test_fetch_metaso_items_returns_empty_when_no_api_key() -> None:
    """Empty api_key short-circuits immediately, no HTTP calls."""
    items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="")
    assert items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_items_returns_empty_when_no_queries() -> None:
    """Empty query list short-circuits immediately."""
    items, success, failed, credits = await fetch_metaso_items("basic_info", [], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 0
    assert credits == 0


async def test_fetch_metaso_items_success() -> None:
    """Returns SearchItems and sums credits on success."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.return_value = ("公司注册资本为人民币壹仟万元整，经营状态正常", 3)
        items, success, failed, credits = await fetch_metaso_items(
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
        mock_q.return_value = ("无", 1)  # too short
        items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 1  # credits still counted even for short answers


async def test_fetch_metaso_items_handles_timeout_gracefully() -> None:
    """Timeout exception is caught; returns empty list, zero credits."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = TimeoutError("timeout")
        items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_items_handles_http_error_gracefully() -> None:
    """HTTP error is caught; returns empty list."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        items, success, failed, credits = await fetch_metaso_items("basic_info", ["query"], api_key="key")
    assert items == []
    assert success == 0
    assert failed == 1
    assert credits == 0


async def test_fetch_metaso_items_multiple_queries_summed() -> None:
    """Credits from multiple queries are summed."""
    with patch("diligence.utils.metaso.query_metaso", new_callable=AsyncMock) as mock_q:
        mock_q.side_effect = [
            ("公司成立于2010年，注册资本为人民币壹仟万元整，经营状态正常", 2),
            ("法定代表人为张三，经营范围包括家具制造与销售，注册地址广东省佛山市", 3),
        ]
        items, success, failed, credits = await fetch_metaso_items("basic_info", ["query1", "query2"], api_key="key")
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
        mock_q.return_value = ("秘塔AI的答案内容，超过20个字符的有效答案", 2)
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
