"""Tests for helpers in summarize_node and search_node not covered by test_nodes.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from diligence.models import DimensionSearchResult, SearchItem, make_item_id
from diligence.nodes.summarize_node import (
    _apply_confidence_floor,
    _extract_json,
    _render_results,
)
from diligence.nodes.search_node import _normalize_target


def _make_item(url: str | None, title: str = "t", snippet: str = "s") -> SearchItem:
    return SearchItem(
        id=make_item_id(url=url, title=title, snippet=snippet),
        title=title,
        url=url,
        snippet=snippet,
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
    )


# ── _normalize_target ─────────────────────────────────────────────────────────


def test_normalize_target_ascii_parens_converted() -> None:
    """ASCII parentheses are replaced with fullwidth equivalents."""
    result = _normalize_target("美世乐(广东)新能源科技有限公司")
    assert "(" not in result
    assert ")" not in result
    assert "（" in result
    assert "）" in result


def test_normalize_target_no_parens_unchanged() -> None:
    """Names without parentheses are returned unchanged."""
    name = "佛山市固特家居制品有限公司"
    assert _normalize_target(name) == name


def test_normalize_target_fullwidth_parens_preserved() -> None:
    """Already-fullwidth parentheses are left as-is."""
    name = "某企业（广东）有限公司"
    assert _normalize_target(name) == name


# ── _extract_json ─────────────────────────────────────────────────────────────


def test_extract_json_bare_object() -> None:
    """Plain JSON object is extracted correctly."""
    raw = '{"summary": "ok", "confidence": "中"}'
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "中"


def test_extract_json_with_code_fence() -> None:
    """JSON wrapped in ```json ... ``` fences is extracted."""
    raw = "```json\n{\"summary\": \"ok\", \"confidence\": \"高\"}\n```"
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "高"


def test_extract_json_with_think_tags() -> None:
    """<think>...</think> blocks are stripped before extraction."""
    raw = "<think>内部推理过程</think>\n{\"summary\": \"摘要\", \"confidence\": \"低\"}"
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "低"


def test_extract_json_think_and_fence_combined() -> None:
    """Both <think> stripping and code fence extraction work together."""
    raw = "<think>reasoning</think>\n```json\n{\"confidence\": \"待核实\"}\n```"
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "待核实"


def test_extract_json_no_json_returns_cleaned_text() -> None:
    """When no JSON object found, the cleaned text is returned (caller handles parse error)."""
    raw = "这不是JSON内容，没有花括号"
    result = _extract_json(raw)
    assert "{" not in result  # no JSON found, text returned as-is


# ── _apply_confidence_floor ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("confidence", "items_count", "all_urls_empty", "status", "expected"),
    [
        # failed status → always 待核实
        ("高", 5, False, "failed", "待核实"),
        # 0 items → always 待核实
        ("高", 0, False, "success", "待核实"),
        # 1 item, AI says 高 → capped at 低
        ("高", 1, False, "success", "低"),
        # 1 item, AI says 中 → capped at 低
        ("中", 1, False, "success", "低"),
        # 1 item, AI says 低 → stays 低
        ("低", 1, False, "success", "低"),
        # All URLs empty → capped at 低
        ("高", 5, True, "success", "低"),
        ("中", 5, True, "success", "低"),
        # Normal case: AI says 高 → stays 高
        ("高", 5, False, "success", "高"),
        # Normal case: AI says 中 → stays 中
        ("中", 3, False, "success", "中"),
        # Unknown confidence string → treated as 待核实 level
        ("未知", 5, False, "success", "待核实"),
    ],
)
def test_apply_confidence_floor(
    confidence: str,
    items_count: int,
    all_urls_empty: bool,
    status: str,
    expected: str,
) -> None:
    result = _apply_confidence_floor(
        confidence, items_count, all_urls_empty=all_urls_empty, status=status
    )
    assert result == expected


# ── _render_results ───────────────────────────────────────────────────────────


def test_render_results_uses_full_text_when_available() -> None:
    """When full_text is set, it is used instead of snippet."""
    item = _make_item("https://example.com/1", snippet="short snippet")
    item = item.model_copy(update={"full_text": "完整的页面正文内容"})
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=[item],
    )
    rendered = _render_results(dsr)
    assert "完整的页面正文内容" in rendered
    assert "full_page" in rendered
    assert "short snippet" not in rendered


def test_render_results_uses_snippet_when_no_full_text() -> None:
    """When full_text is empty, snippet is used."""
    item = _make_item("https://example.com/1", snippet="摘要内容")
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=[item],
    )
    rendered = _render_results(dsr)
    assert "摘要内容" in rendered
    assert "snippet" in rendered


def test_render_results_empty_items() -> None:
    """Empty items list returns a sentinel '(no search results)' string."""
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=[],
    )
    rendered = _render_results(dsr)
    assert "no search results" in rendered


def test_render_results_multiple_items_separated() -> None:
    """Multiple items are separated by '---' delimiters."""
    items = [
        _make_item(f"https://example.com/{i}", title=f"标题{i}") for i in range(3)
    ]
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=items,
    )
    rendered = _render_results(dsr)
    assert rendered.count("---") == 2  # 3 items → 2 separators
