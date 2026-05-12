"""Tests for helpers in summarize_node and search_node not covered by test_nodes.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from diligence.config import ExtractField
from diligence.models import DimensionSearchResult, SearchItem, make_item_id
from diligence.nodes.summarize_node import (
    _ExtractionSource,
    _FieldExtraction,
    _ExtractionsResult,
    _apply_confidence_floor,
    _apply_snippet_confidence_cap,
    _build_field_descriptions,
    _build_extraction_prompt,
    _downgrade_confidence,
    _extract_json,
    _field_kind,
    _format_extraction_table,
    _looks_like_capital,
    _looks_like_date,
    _looks_like_phone,
    _render_results,
    _select_extraction_sources,
    _validate_credit_code,
    _validate_email,
    _validate_extractions,
    _validate_url,
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
    raw = '```json\n{"summary": "ok", "confidence": "高"}\n```'
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "高"


def test_extract_json_with_think_tags() -> None:
    """<think>...</think> blocks are stripped before extraction."""
    raw = '<think>内部推理过程</think>\n{"summary": "摘要", "confidence": "低"}'
    result = _extract_json(raw)
    parsed = json.loads(result)
    assert parsed["confidence"] == "低"


def test_extract_json_think_and_fence_combined() -> None:
    """Both <think> stripping and code fence extraction work together."""
    raw = '<think>reasoning</think>\n```json\n{"confidence": "待核实"}\n```'
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
    result = _apply_confidence_floor(confidence, items_count, all_urls_empty=all_urls_empty, status=status)
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
    items = [_make_item(f"https://example.com/{i}", title=f"标题{i}") for i in range(3)]
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=items,
    )
    rendered = _render_results(dsr)
    assert rendered.count("---") == 2  # 3 items → 2 separators


# ── _build_field_descriptions ──────────────────────────────────────────────────


def test_build_field_descriptions_basic() -> None:
    fields = [
        ExtractField(field_name="统一社会信用代码", description="18位字母数字组合"),
        ExtractField(field_name="法定代表人", description="法定代表人姓名"),
    ]
    result = _build_field_descriptions(fields)
    assert "1. 统一社会信用代码：18位字母数字组合" in result
    assert "2. 法定代表人：法定代表人姓名" in result


def test_build_field_descriptions_with_examples() -> None:
    fields = [
        ExtractField(field_name="统一社会信用代码", description="18位", examples="91440605682473330H"),
    ]
    result = _build_field_descriptions(fields)
    assert "（示例：91440605682473330H）" in result


def test_build_field_descriptions_empty() -> None:
    assert _build_field_descriptions([]) == ""


# ── _build_extraction_prompt ───────────────────────────────────────────────────


def test_build_extraction_prompt_includes_target() -> None:
    item = _make_item("https://a.com", title="T", snippet="s")
    item = item.model_copy(update={"full_text": "正文内容"})
    sources = [_ExtractionSource(item=item, content=item.full_text, content_type="full_text", evidence_weight="high")]
    fields = [ExtractField(field_name="代码", description="desc")]
    template = "{target}\n{field_descriptions}\n{count}\n{item_contents}"
    result = _build_extraction_prompt("测试公司", fields, sources, template)
    assert result.startswith("测试公司")
    assert "正文内容" in result
    assert "1" in result  # count


def test_build_extraction_prompt_only_full_text_items() -> None:
    """Only items with full_text are included (via _select_extraction_sources)."""
    item_ft = _make_item("https://a.com", title="A", snippet="s")
    item_ft = item_ft.model_copy(update={"full_text": "完整页面"})
    item_no_ft = _make_item("https://b.com", title="B", snippet="摘要")
    fields = [ExtractField(field_name="f", description="d")]
    template = "{target}\n{field_descriptions}\n{count}\n{item_contents}"
    sources = _select_extraction_sources([item_ft, item_no_ft])
    result = _build_extraction_prompt("目标", fields, sources, template)
    assert "完整页面" in result
    assert result.count("[来源") == 1  # only ft item included


# ── _format_extraction_table ───────────────────────────────────────────────────


def test_format_extraction_table_with_data() -> None:
    extractions = _ExtractionsResult(
        extractions={
            "统一社会信用代码": [
                _FieldExtraction(
                    source_item_id="abc123", source_url="https://curtao.com/p",
                    value="91440605682473330H", confidence="中",
                ),
            ],
            "法定代表人": [
                _FieldExtraction(
                    source_item_id="abc123", source_url="https://curtao.com/p",
                    value="欧泽超", confidence="中",
                ),
            ],
        }
    )
    result = _format_extraction_table(extractions)
    assert "结构化字段提取结果" in result
    assert "91440605682473330H" in result
    assert "欧泽超" in result
    assert "abc123" in result


def test_format_extraction_table_empty_field() -> None:
    extractions = _ExtractionsResult(extractions={"某字段": []})
    result = _format_extraction_table(extractions)
    assert "*未找到*" in result


def test_format_extraction_table_no_fields() -> None:
    extractions = _ExtractionsResult(extractions={})
    result = _format_extraction_table(extractions)
    # Should produce table header with no data rows
    assert "结构化字段提取结果" in result


# ── _render_results with extraction_table ──────────────────────────────────────


def test_render_results_with_extraction_table_truncates_full_text() -> None:
    """When extraction_table is provided, full_text is truncated."""
    long_text = "x" * 5000
    item = _make_item("https://a.com", title="T", snippet="s")
    item = item.model_copy(update={"full_text": long_text})
    dsr = DimensionSearchResult(
        dimension_id="d", dimension_name="n", status="success", items=[item],
    )
    rendered = _render_results(dsr, extraction_table="## 提取表\n|...|")
    assert "已截断" in rendered
    assert long_text not in rendered  # full text truncated


def test_render_results_with_extraction_table_preserves_snippets() -> None:
    """Snippet-only items are never truncated."""
    item = _make_item("https://a.com", title="T", snippet="短摘要")
    dsr = DimensionSearchResult(
        dimension_id="d", dimension_name="n", status="success", items=[item],
    )
    rendered = _render_results(dsr, extraction_table="## 提取表\n|...|")
    assert "短摘要" in rendered
    assert "已截断" not in rendered  # snippet is not truncated


def test_render_results_no_extraction_table_full_text_not_truncated() -> None:
    """Without extraction_table, behavior is unchanged (no truncation)."""
    long_text = "y" * 3000
    item = _make_item("https://a.com", title="T", snippet="s")
    item = item.model_copy(update={"full_text": long_text})
    dsr = DimensionSearchResult(
        dimension_id="d", dimension_name="n", status="success", items=[item],
    )
    rendered = _render_results(dsr)
    assert long_text in rendered
    assert "已截断" not in rendered


# ── _select_extraction_sources ──────────────────────────────────────────────────


def test_select_extraction_sources_full_text_priority() -> None:
    """full_text sources come first and take precedence over snippets."""
    item_ft = _make_item("https://a.com", title="A", snippet="s1")
    item_ft = item_ft.model_copy(update={"full_text": "完整页面内容"})
    item_snip = _make_item(
        "https://b.com", title="B", snippet="搜索摘要B-这是一个足够长的摘要内容以确保超过最小长度限制"
    )
    sources = _select_extraction_sources([item_ft, item_snip])
    assert len(sources) == 2
    assert sources[0].content_type == "full_text"
    assert sources[1].content_type == "snippet"


def test_select_extraction_sources_snippet_min_length_filtered() -> None:
    """Snippets shorter than _SNIPPET_MIN_LENGTH (20 chars) are skipped."""
    item_ft = _make_item("https://a.com", title="A", snippet="短")
    item_ft = item_ft.model_copy(update={"full_text": "内容"})
    item_short = _make_item("https://b.com", title="B", snippet="太短了")
    sources = _select_extraction_sources([item_ft, item_short])
    assert len(sources) == 1
    assert sources[0].content_type == "full_text"


def test_select_extraction_sources_no_duplicate_urls() -> None:
    """Snippet from same URL as full_text is skipped."""
    item_ft = _make_item("https://a.com", title="A", snippet="s1")
    item_ft = item_ft.model_copy(update={"full_text": "内容"})
    item_dup = _make_item("https://a.com", title="A2", snippet="重复摘要足够长1234567890")
    sources = _select_extraction_sources([item_ft, item_dup])
    assert len(sources) == 1
    assert sources[0].content_type == "full_text"


def test_select_extraction_sources_snippet_fallback_limit() -> None:
    """Snippet fallback is capped at _MAX_SNIPPET_FALLBACK_ITEMS (8)."""
    items = []
    for i in range(12):
        item = _make_item(f"https://example.com/{i}", title=f"T{i}", snippet=f"足够长的摘要内容第{i}项1234567890")
        items.append(item)
    sources = _select_extraction_sources(items)
    assert len(sources) == 8  # capped at _MAX_SNIPPET_FALLBACK_ITEMS


def test_select_extraction_sources_empty() -> None:
    """No items returns empty list."""
    assert _select_extraction_sources([]) == []


def test_select_extraction_sources_snippet_only() -> None:
    """When no full_text, fallback to snippets up to limit."""
    items = [
        _make_item(f"https://example.com/{i}", title=f"T{i}", snippet=f"足够长的摘要内容第{i}项-1234567890")
        for i in range(3)
    ]
    sources = _select_extraction_sources(items)
    assert len(sources) == 3
    assert all(s.content_type == "snippet" for s in sources)
    assert all(s.evidence_weight == "low" for s in sources)


# ── _build_extraction_prompt with snippet sources ───────────────────────────────


def test_build_extraction_prompt_marks_snippet_low_weight() -> None:
    """Snippet sources are annotated with evidence_weight=low and a warning."""
    item = _make_item("https://b.com", title="B", snippet="足够长的摘要内容需要超过20字")
    sources = [_ExtractionSource(
        item=item, content=item.snippet, content_type="snippet", evidence_weight="low",
    )]
    fields = [ExtractField(field_name="f", description="d")]
    template = "{target}\n{field_descriptions}\n{count}\n{item_contents}"
    result = _build_extraction_prompt("目标", fields, sources, template)
    assert "证据权重: low" in result
    assert "内容类型: snippet" in result
    assert "低置信度" in result


def test_build_extraction_prompt_full_text_high_weight() -> None:
    """Full-text sources are annotated with evidence_weight=high, no snippet warning."""
    item = _make_item("https://a.com", title="A", snippet="s")
    item = item.model_copy(update={"full_text": "正文"})
    sources = [_ExtractionSource(
        item=item, content=item.full_text, content_type="full_text", evidence_weight="high",
    )]
    fields = [ExtractField(field_name="f", description="d")]
    template = "{target}\n{field_descriptions}\n{count}\n{item_contents}"
    result = _build_extraction_prompt("目标", fields, sources, template)
    assert "证据权重: high" in result
    assert "内容类型: full_text" in result
    assert "低置信度" not in result


# ── _apply_snippet_confidence_cap ───────────────────────────────────────────────


def test_apply_snippet_confidence_cap_downgrades_snippet_only_field() -> None:
    """Field with all candidates from snippet sources: 高→低."""
    item_a = _make_item("https://a.com", title="A", snippet="足够长的摘要内容需要超过20字")
    item_b = _make_item("https://b.com", title="B", snippet="另一个足够长的摘要内容超过20字")
    sources = [
        _ExtractionSource(item=item_a, content=item_a.snippet, content_type="snippet", evidence_weight="low"),
        _ExtractionSource(item=item_b, content=item_b.snippet, content_type="snippet", evidence_weight="low"),
    ]
    extractions = _ExtractionsResult(extractions={
        "法定代表人": [
            _FieldExtraction(source_item_id=item_a.id, source_url="https://a.com", value="张三", confidence="高"),
            _FieldExtraction(source_item_id=item_b.id, source_url="https://b.com", value="张三", confidence="中"),
        ],
    })
    _apply_snippet_confidence_cap(extractions, sources)
    assert extractions.extractions["法定代表人"][0].confidence == "低"  # 高→低
    assert extractions.extractions["法定代表人"][1].confidence == "中"  # 中 unchanged


def test_apply_snippet_confidence_cap_preserves_full_text_field() -> None:
    """Field with at least one full_text source keeps original confidence."""
    item_ft = _make_item("https://a.com", title="A", snippet="s")
    item_ft = item_ft.model_copy(update={"full_text": "完整页面"})
    item_snip = _make_item("https://b.com", title="B", snippet="足够长的摘要内容超过20字")
    sources = [
        _ExtractionSource(item=item_ft, content=item_ft.full_text, content_type="full_text", evidence_weight="high"),
        _ExtractionSource(item=item_snip, content=item_snip.snippet, content_type="snippet", evidence_weight="low"),
    ]
    extractions = _ExtractionsResult(extractions={
        "统一社会信用代码": [
            _FieldExtraction(
                source_item_id=item_ft.id, source_url="https://a.com", value="91440605", confidence="高",
            ),
            _FieldExtraction(
                source_item_id=item_snip.id, source_url="https://b.com", value="91440605", confidence="中",
            ),
        ],
    })
    _apply_snippet_confidence_cap(extractions, sources)
    # Neither downgraded because ft source is among candidates
    assert extractions.extractions["统一社会信用代码"][0].confidence == "高"
    assert extractions.extractions["统一社会信用代码"][1].confidence == "中"


def test_apply_snippet_confidence_cap_empty_field_skipped() -> None:
    """Empty candidate list is skipped without error."""
    item = _make_item("https://a.com", title="A", snippet="足够长的摘要内容超过20字")
    sources = [_ExtractionSource(item=item, content=item.snippet, content_type="snippet", evidence_weight="low")]
    extractions = _ExtractionsResult(extractions={"某字段": []})
    _apply_snippet_confidence_cap(extractions, sources)  # should not raise


def test_apply_snippet_confidence_cap_all_snippets_no_full_text() -> None:
    """When no full_text sources exist at all, snippet-only fields are still capped."""
    item_a = _make_item("https://a.com", title="A", snippet="足够长的摘要内容需要超过20字")
    sources = [_ExtractionSource(item=item_a, content=item_a.snippet, content_type="snippet", evidence_weight="low")]
    extractions = _ExtractionsResult(extractions={
        "法定代表人": [
            _FieldExtraction(source_item_id=item_a.id, source_url="https://a.com", value="李四", confidence="高"),
        ],
    })
    _apply_snippet_confidence_cap(extractions, sources)
    assert extractions.extractions["法定代表人"][0].confidence == "低"


def test_snippet_cap_after_validation_full_text_candidate_removed() -> None:
    """Snippet cap runs AFTER validation: full_text candidate deleted → snippet 高→低.

    If a field has both a full_text candidate (invalid, deleted by validation)
    and a snippet candidate with 高, the remaining snippet-only field must be
    downgraded to 低.
    """
    item_ft = _make_item("https://a.com", title="A", snippet="s")
    item_ft = item_ft.model_copy(update={"full_text": "完整页面内容但信用代码是无效值"})
    item_snip = _make_item(
        "https://b.com", title="B", snippet="足够长的摘要内容超过20字包含信用代码"
    )
    sources = [
        _ExtractionSource(item=item_ft, content=item_ft.full_text, content_type="full_text", evidence_weight="high"),
        _ExtractionSource(item=item_snip, content=item_snip.snippet, content_type="snippet", evidence_weight="low"),
    ]
    extractions = _ExtractionsResult(extractions={
        "统一社会信用代码": [
            _FieldExtraction(
                source_item_id=item_ft.id, source_url="https://a.com",
                value="无信用代码信息", confidence="高",
            ),
            _FieldExtraction(
                source_item_id=item_snip.id, source_url="https://b.com",
                value="91440605682473330H", confidence="高",
            ),
        ],
    })
    # Correct order: validate first (deletes full_text candidate), then cap
    _validate_extractions(extractions)
    _apply_snippet_confidence_cap(extractions, sources)

    codes = extractions.extractions["统一社会信用代码"]
    assert len(codes) == 1
    assert codes[0].source_item_id == item_snip.id
    assert codes[0].confidence == "低", (
        f"Expected 低 after snippet cap (full_text candidate was deleted), got {codes[0].confidence}"
    )


# ── hallucinated ID filtering (valid_ids from sources, not items) ──────────────


def test_select_extraction_sources_excludes_short_snippet_from_valid_ids() -> None:
    """Item excluded from sources must not appear in valid_ids for hallucination filter.

    If an item's snippet is too short to be a fallback source, its id should not
    be considered valid when filtering LLM-returned source_item_ids.  Otherwise
    the LLM could hallucinate a reference to an item that was never in the prompt.
    """
    item_ft = _make_item("https://a.com", title="A", snippet="s1")
    item_ft = item_ft.model_copy(update={"full_text": "完整页面"})
    item_short = _make_item("https://b.com", title="B", snippet="短")  # < 20 chars

    sources = _select_extraction_sources([item_ft, item_short])
    valid_ids = {s.item.id for s in sources}

    assert item_ft.id in valid_ids  # full_text source is valid
    assert item_short.id not in valid_ids  # excluded, must not be valid


# ── _field_kind ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field_name", "expected"),
    [
        ("统一社会信用代码", "credit_code"),
        ("信用代码", "credit_code"),
        ("成立日期", "date"),
        ("营业期限", "date"),
        ("电子邮箱", "email"),
        ("联系电话", "phone"),
        ("官网", "url"),
        ("来源URL", "url"),
        ("网址", "url"),
        ("注册资本", "capital"),
        ("实缴资本", "capital"),
        ("法定代表人", "unknown"),
        ("经营范围", "unknown"),
    ],
)
def test_field_kind(field_name: str, expected: str) -> None:
    assert _field_kind(field_name) == expected


# ── _downgrade_confidence ──────────────────────────────────────────────────────


def test_downgrade_confidence_reduces_高_to_低() -> None:
    fe = _FieldExtraction(source_item_id="a", source_url="x", value="v", confidence="高")
    _downgrade_confidence(fe)
    assert fe.confidence == "低"


def test_downgrade_confidence_reduces_中_to_低() -> None:
    fe = _FieldExtraction(source_item_id="a", source_url="x", value="v", confidence="中")
    _downgrade_confidence(fe)
    assert fe.confidence == "低"


def test_downgrade_confidence_leaves_低_unchanged() -> None:
    fe = _FieldExtraction(source_item_id="a", source_url="x", value="v", confidence="低")
    _downgrade_confidence(fe)
    assert fe.confidence == "低"


# ── _validate_credit_code ──────────────────────────────────────────────────────


def test_validate_credit_code_extracts_18_chars() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="统一社会信用代码：91440605682473330H", confidence="高",
    )
    assert _validate_credit_code(c) is True
    assert c.value == "91440605682473330H"


def test_validate_credit_code_lowercased_input() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="91440605682473330h", confidence="高",
    )
    assert _validate_credit_code(c) is True
    assert c.value == "91440605682473330H"


def test_validate_credit_code_removes_invalid() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="暂无统一社会信用代码信息", confidence="中",
    )
    assert _validate_credit_code(c) is False


# ── _validate_email ────────────────────────────────────────────────────────────


def test_validate_email_extracts_addresses() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="联系邮箱：hr@example.com", confidence="高",
    )
    assert _validate_email(c) is True
    assert "hr@example.com" in c.value


def test_validate_email_multiple_addresses() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="邮箱1: a@x.com 邮箱2: b@y.cn", confidence="高",
    )
    assert _validate_email(c) is True
    assert "a@x.com" in c.value
    assert "b@y.cn" in c.value


def test_validate_email_removes_invalid() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="未找到邮箱信息", confidence="中",
    )
    assert _validate_email(c) is False


# ── _looks_like_phone ──────────────────────────────────────────────────────────


def test_looks_like_phone_mobile() -> None:
    assert _looks_like_phone("13812345678") is True


def test_looks_like_phone_landline() -> None:
    assert _looks_like_phone("0757-89992385") is True


def test_looks_like_phone_400() -> None:
    assert _looks_like_phone("400-123-4567") is True


def test_looks_like_phone_invalid() -> None:
    assert _looks_like_phone("暂无联系电话") is False


# ── _validate_url ──────────────────────────────────────────────────────────────


def test_validate_url_extracts_and_normalizes_http() -> None:
    """Full URL is extracted from prefix text and confidence stays 高."""
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="官网：https://www.example.com", confidence="高",
    )
    assert _validate_url(c, strict=False) is True
    assert c.value == "https://www.example.com"
    assert c.confidence == "高"


def test_validate_url_strict_deletes_non_url() -> None:
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="未找到官网信息", confidence="中",
    )
    assert _validate_url(c, strict=True) is False


def test_validate_url_non_strict_bare_www_domain_downgraded() -> None:
    """Bare www. domain without scheme is kept but downgraded to 低."""
    c = _FieldExtraction(
        source_item_id="a", source_url="x",
        value="www.example.com", confidence="高",
    )
    assert _validate_url(c, strict=False) is True
    assert c.confidence == "低"
    assert c.value == "www.example.com"


# ── _looks_like_date ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "2013-01-18", "2013年1月18日", "2013/01/18", "2013.01.18",
    "2013年", "长期", "2013-01-18至长期",
])
def test_looks_like_date_valid(value: str) -> None:
    assert _looks_like_date(value) is True


def test_looks_like_date_invalid() -> None:
    assert _looks_like_date("暂无日期信息") is False


# ── _looks_like_capital ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "100万", "5000万元", "1.5亿元", "200万人民币", "1000万美元",
])
def test_looks_like_capital_valid(value: str) -> None:
    assert _looks_like_capital(value) is True


def test_looks_like_capital_invalid() -> None:
    assert _looks_like_capital("未披露") is False


# ── _validate_extractions ──────────────────────────────────────────────────────


def test_validate_extractions_removes_placeholder_values() -> None:
    extractions = _ExtractionsResult(extractions={
        "法定代表人": [
            _FieldExtraction(source_item_id="a", source_url="x", value="未找到", confidence="中"),
            _FieldExtraction(source_item_id="a", source_url="x", value="暂无", confidence="低"),
        ],
    })
    _validate_extractions(extractions)
    assert "法定代表人" not in extractions.extractions  # all removed


def test_validate_extractions_unknown_field_passthrough() -> None:
    """Fields with kind='unknown' are not validated, kept as-is."""
    extractions = _ExtractionsResult(extractions={
        "经营范围": [
            _FieldExtraction(source_item_id="a", source_url="x", value="制造销售电子产品", confidence="高"),
        ],
    })
    _validate_extractions(extractions)
    assert "经营范围" in extractions.extractions
    assert extractions.extractions["经营范围"][0].value == "制造销售电子产品"
    assert extractions.extractions["经营范围"][0].confidence == "高"


def test_validate_extractions_credit_code_flow() -> None:
    """Integration: valid code extracted, invalid one removed."""
    extractions = _ExtractionsResult(extractions={
        "统一社会信用代码": [
            _FieldExtraction(
                source_item_id="a", source_url="x",
                value="代码：91440605682473330H", confidence="高",
            ),
            _FieldExtraction(
                source_item_id="b", source_url="y",
                value="未找到相关信息", confidence="中",
            ),
        ],
    })
    _validate_extractions(extractions)
    codes = extractions.extractions["统一社会信用代码"]
    assert len(codes) == 1
    assert codes[0].value == "91440605682473330H"


def test_validate_extractions_phone_downgrades_invalid() -> None:
    extractions = _ExtractionsResult(extractions={
        "联系电话": [
            _FieldExtraction(source_item_id="a", source_url="x", value="0757-89992385", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="暂未获取到电话", confidence="中"),
        ],
    })
    _validate_extractions(extractions)
    phones = extractions.extractions["联系电话"]
    assert len(phones) == 2  # both kept (invalid is downgraded, not deleted)
    assert phones[0].confidence == "高"  # valid stays
    assert phones[1].confidence == "低"  # invalid downgraded


def test_validate_extractions_date_downgrades_invalid() -> None:
    extractions = _ExtractionsResult(extractions={
        "成立日期": [
            _FieldExtraction(source_item_id="a", source_url="x", value="2013-01-18", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="未知日期", confidence="中"),
        ],
    })
    _validate_extractions(extractions)
    dates = extractions.extractions["成立日期"]
    assert len(dates) == 2
    assert dates[0].confidence == "高"
    assert dates[1].confidence == "低"


def test_validate_extractions_capital_downgrades_non_numeric() -> None:
    extractions = _ExtractionsResult(extractions={
        "注册资本": [
            _FieldExtraction(source_item_id="a", source_url="x", value="500万元人民币", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="不详", confidence="中"),
        ],
    })
    _validate_extractions(extractions)
    capital = extractions.extractions["注册资本"]
    assert len(capital) == 1  # placeholder deleted
    assert capital[0].value == "500万元人民币"
    assert capital[0].confidence == "高"


def test_validate_extractions_url_strict_removes_non_url() -> None:
    extractions = _ExtractionsResult(extractions={
        "来源URL": [
            _FieldExtraction(source_item_id="a", source_url="x", value="https://qcc.com/p/1", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="信息缺失", confidence="中"),
        ],
    })
    _validate_extractions(extractions)
    urls = extractions.extractions["来源URL"]
    assert len(urls) == 1
    assert urls[0].value == "https://qcc.com/p/1"


# ── _ValidationStats ─────────────────────────────────────────────────────────


def test_validation_stats_placeholder_removed() -> None:
    extractions = _ExtractionsResult(extractions={
        "法定代表人": [
            _FieldExtraction(source_item_id="a", source_url="x", value="未找到", confidence="中"),
            _FieldExtraction(source_item_id="a", source_url="x", value="暂无", confidence="低"),
            _FieldExtraction(source_item_id="a", source_url="x", value="欧泽超", confidence="高"),
        ],
    })
    stats = _validate_extractions(extractions)
    assert stats.removed == 2
    assert stats.downgraded == 0
    assert stats.normalized == 0


def test_validation_stats_credit_code_normalized() -> None:
    extractions = _ExtractionsResult(extractions={
        "统一社会信用代码": [
            _FieldExtraction(
                source_item_id="a", source_url="x",
                value="代码：91440605682473330H", confidence="高",
            ),
        ],
    })
    stats = _validate_extractions(extractions)
    assert stats.removed == 0
    assert stats.normalized == 1
    assert extractions.extractions["统一社会信用代码"][0].value == "91440605682473330H"


def test_validation_stats_email_normalized() -> None:
    extractions = _ExtractionsResult(extractions={
        "电子邮箱": [
            _FieldExtraction(
                source_item_id="a", source_url="x",
                value="联系邮箱：hr@example.com", confidence="高",
            ),
        ],
    })
    stats = _validate_extractions(extractions)
    assert stats.normalized == 1
    assert "hr@example.com" in extractions.extractions["电子邮箱"][0].value


def test_validation_stats_phone_downgraded() -> None:
    extractions = _ExtractionsResult(extractions={
        "联系电话": [
            _FieldExtraction(source_item_id="a", source_url="x", value="0757-89992385", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="暂未获取到电话", confidence="中"),
        ],
    })
    stats = _validate_extractions(extractions)
    assert stats.downgraded == 1
    assert stats.removed == 0


def test_validation_stats_strict_url_removed() -> None:
    extractions = _ExtractionsResult(extractions={
        "来源URL": [
            _FieldExtraction(source_item_id="a", source_url="x", value="https://qcc.com/p/1", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="信息缺失", confidence="中"),
        ],
    })
    stats = _validate_extractions(extractions)
    assert stats.removed == 1  # invalid URL deleted
    assert stats.downgraded == 0


def test_validation_stats_mixed_fields() -> None:
    """Stats aggregate correctly across multiple field kinds."""
    extractions = _ExtractionsResult(extractions={
        "统一社会信用代码": [
            _FieldExtraction(
                source_item_id="a", source_url="x",
                value="信用代码：91440605682473330H", confidence="高",
            ),
            _FieldExtraction(
                source_item_id="b", source_url="y",
                value="无信用代码信息", confidence="中",
            ),
        ],
        "联系电话": [
            _FieldExtraction(source_item_id="a", source_url="x", value="0757-89992385", confidence="高"),
            _FieldExtraction(source_item_id="b", source_url="y", value="暂未获取", confidence="中"),
        ],
        "法定代表人": [
            _FieldExtraction(source_item_id="a", source_url="x", value="未找到", confidence="低"),
        ],
    })
    stats = _validate_extractions(extractions)
    # removed: 1 invalid credit_code + 1 placeholder
    assert stats.removed == 2
    # normalized: 1 credit_code value extracted
    assert stats.normalized == 1
    # downgraded: 1 phone
    assert stats.downgraded == 1
