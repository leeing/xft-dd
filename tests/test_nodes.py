from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from diligence.config import AppConfig, Dimension
from diligence.models import (
    DimensionSearchResult,
    DimensionSummary,
    SearchItem,
    make_item_id,
)
from diligence.state import DiligenceState


def _make_cfg() -> AppConfig:
    return AppConfig(
        model="MiniMax-M2.7-Highspeed",
        merge_prompt="请综合{summaries}生成{target}的报告",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                search_queries=["{target} 工商注册"],
                summary_prompt="请分析{target}的工商信息。\n{results}",
            )
        ],
    )


def _make_search_result(
    dim_id: str = "basic_info",
    n_items: int = 2,
    all_urls_empty: bool = False,
) -> DimensionSearchResult:
    items = []
    for i in range(n_items):
        url = None if all_urls_empty else f"https://example.com/{i}"
        title = f"标题{i}"
        snippet = f"片段{i}"
        items.append(
            SearchItem(
                id=make_item_id(url=url, title=title, snippet=snippet),
                title=title,
                url=url,
                snippet=snippet,
                query="q",
                dimension_id=dim_id,
                rank=i,
                fetched_at=datetime.now(UTC),
            )
        )
    return DimensionSearchResult(
        dimension_id=dim_id,
        dimension_name="工商基本信息",
        status="success",
        items=items,
    )


def _base_state(cfg: AppConfig, tmp_path: Path) -> DiligenceState:
    dim = cfg.dimensions[0]
    return DiligenceState(
        target="某公司",
        config=cfg,
        run_id="test-run",
        active_dimensions=[dim],
        output_dir=str(tmp_path),
        current_dimension=dim,
        search_results_by_dimension={},
        summaries_by_dimension={},
        errors=[],
        report="",
        report_path="",
        artifacts_dir="",
    )


# -- search_node --


async def test_search_node_success(tmp_path: Path) -> None:
    from diligence.nodes.search_node import search_node

    cfg = _make_cfg()
    mmx_output = json.dumps(
        {
            "organic": [
                {"title": "A", "link": "https://qcc.com/1", "snippet": "注册资本"},
            ]
        }
    ).encode()

    async def fake_exec(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(mmx_output, b""))
        return proc

    state = _base_state(cfg, tmp_path)
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await search_node(state)

    dsr = result["search_results_by_dimension"]["basic_info"]
    assert isinstance(dsr, DimensionSearchResult)
    assert dsr.status == "success"
    assert len(dsr.items) == 1


async def test_search_node_timeout_produces_partial(tmp_path: Path) -> None:
    from diligence.nodes.search_node import search_node

    cfg = AppConfig(
        model="m",
        merge_prompt="x",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                search_queries=["{target} 工商", "{target} 信用代码"],
                summary_prompt="x\n{results}",
            )
        ],
    )
    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = MagicMock()
        if call_count == 1:
            proc.communicate = AsyncMock(
                return_value=(
                    json.dumps({"organic": [{"title": "T", "link": "https://a.com", "snippet": "s"}]}).encode(),
                    b"",
                )
            )
        else:

            async def slow():
                await asyncio.sleep(999)
                return (b"", b"")

            proc.communicate = slow
        return proc

    state = _base_state(cfg, tmp_path)
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await search_node(state)

    dsr = result["search_results_by_dimension"]["basic_info"]
    assert dsr.status == "partial"
    assert len(dsr.items) == 1


# -- summarize_node --


async def test_summarize_node_success(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    dsr = _make_search_result()
    ai_response = json.dumps(
        {
            "summary": "某公司成立于2010年",
            "confidence": "中",
            "uncertain_facts": [],
            "evidence_item_ids": [dsr.items[0].id],
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_response))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert isinstance(summary, DimensionSummary)
    assert summary.confidence == "中"
    assert summary.status == "success"


async def test_summarize_node_zero_results_forces_待核实(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=[],
    )
    ai_response = json.dumps(
        {
            "summary": "无信息",
            "confidence": "中",
            "uncertain_facts": [],
            "evidence_item_ids": [],
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_response))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert summary.confidence == "待核实"


async def test_summarize_node_one_result_caps_at_低(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    dsr = _make_search_result(n_items=1)
    ai_response = json.dumps(
        {
            "summary": "一条结果",
            "confidence": "高",
            "uncertain_facts": [],
            "evidence_item_ids": [dsr.items[0].id],
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_response))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert summary.confidence in ("低", "待核实")


async def test_summarize_node_json_parse_failure_fallback(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    dsr = _make_search_result(n_items=3)
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="这不是JSON内容"))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert summary.confidence == "待核实"
    assert summary.status == "partial"
    assert len(result.get("errors", [])) > 0
    assert len(summary.summary) <= 1600


async def test_summarize_node_fallback_truncates_at_1500(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    long_snippet = "x" * 600
    items = [
        SearchItem(
            id=make_item_id(url=f"https://example.com/{i}", title=f"t{i}", snippet=long_snippet),
            title=f"t{i}",
            url=f"https://example.com/{i}",
            snippet=long_snippet,
            query="q",
            dimension_id="basic_info",
            rank=i,
            fetched_at=datetime.now(UTC),
        )
        for i in range(4)
    ]
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        items=items,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="not json"))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert len(summary.summary) <= 1600
    assert "以下为部分原始搜索片段" in summary.summary


async def test_summarize_node_hallucinated_ids_filtered(tmp_path: Path) -> None:
    from diligence.nodes.summarize_node import summarize_node

    cfg = _make_cfg()
    dsr = _make_search_result(n_items=2)
    ai_response = json.dumps(
        {
            "summary": "摘要",
            "confidence": "中",
            "uncertain_facts": [],
            "evidence_item_ids": [dsr.items[0].id, "hallucinated000"],
        }
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_response))])
    )

    state = _base_state(cfg, tmp_path)
    state["search_results_by_dimension"] = {"basic_info": dsr}

    with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
        result = await summarize_node(state)

    summary = result["summaries_by_dimension"]["basic_info"]
    assert "hallucinated000" not in summary.evidence_item_ids
    assert dsr.items[0].id in summary.evidence_item_ids


# -- collect_node --


async def test_collect_node_all_present(tmp_path: Path) -> None:
    from diligence.nodes.collect_node import collect_node

    cfg = _make_cfg()
    summary = DimensionSummary(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="success",
        summary="ok",
        confidence="中",
        uncertain_facts=[],
        evidence_item_ids=[],
    )
    state = _base_state(cfg, tmp_path)
    state["summaries_by_dimension"] = {"basic_info": summary}
    state["current_dimension"] = None

    result = collect_node(state)
    assert result.get("errors", []) == []


async def test_collect_node_required_failed_produces_error(tmp_path: Path) -> None:
    from diligence.nodes.collect_node import collect_node

    cfg = _make_cfg()
    failed_summary = DimensionSummary(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="failed",
        summary="搜索失败，建议人工核查",
        confidence="待核实",
        uncertain_facts=["搜索失败"],
        evidence_item_ids=[],
        error="all queries failed",
    )
    state = _base_state(cfg, tmp_path)
    state["summaries_by_dimension"] = {"basic_info": failed_summary}
    state["current_dimension"] = None

    result = collect_node(state)
    errors = result.get("errors", [])
    assert any("核心" in e.message or "required" in e.message.lower() for e in errors)
