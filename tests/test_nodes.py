from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

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
        merge_prompt="请综合{summaries}生成{target}的报告",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["{target} 工商注册"],
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
        started_at=datetime.now(UTC),
        active_dimensions=[dim],
        output_dir=str(tmp_path),
        current_dimension=dim,
        search_results_by_dimension={},
        summaries_by_dimension={},
        errors=[],
        report="",
        report_path="",
        artifacts_dir="",
        all_dimension_names={},
    )


# -- search_node --


async def test_search_node_success(tmp_path: Path) -> None:
    from diligence.nodes.search_node import search_node

    cfg = _make_cfg()
    organic = [{"title": "A", "link": "https://qcc.com/1", "snippet": "注册资本", "date": ""}]

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = {"organic": organic}
    mock_resp.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    state = _base_state(cfg, tmp_path)
    with patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        result = await search_node(state)

    dsr = result["search_results_by_dimension"]["basic_info"]
    assert isinstance(dsr, DimensionSearchResult)
    assert dsr.status == "success"
    assert len(dsr.items) == 1


async def test_search_node_timeout_produces_partial(tmp_path: Path) -> None:
    from diligence.nodes.search_node import search_node

    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["{target} 工商", "{target} 信用代码"],
                summary_prompt="x\n{results}",
            )
        ],
    )
    call_count = 0

    def make_client(**_kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        if call_count == 1:
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.json.return_value = {
                "organic": [{"title": "T", "link": "https://a.com", "snippet": "s", "date": ""}]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
        else:
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        return mock_client

    state = _base_state(cfg, tmp_path)
    with patch("diligence.utils.minimax_search.httpx.AsyncClient", side_effect=make_client):
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
    mock_client.chat.completions.create = AsyncMock(
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
    mock_client.chat.completions.create = AsyncMock(
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
    mock_client.chat.completions.create = AsyncMock(
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
    mock_client.chat.completions.create = AsyncMock(
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
    mock_client.chat.completions.create = AsyncMock(
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
    mock_client.chat.completions.create = AsyncMock(
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


# -- init_node --


def test_init_node_sets_started_at(tmp_path: Path) -> None:
    from diligence.nodes.init_node import init_node

    cfg = _make_cfg()
    state = _base_state(cfg, tmp_path)
    result = init_node(state)
    assert "started_at" in result
    started = result["started_at"]
    assert isinstance(started, datetime)
    assert started.tzinfo is not None  # must be timezone-aware


def test_save_node_uses_state_started_at(tmp_path: Path) -> None:
    from diligence.models import CostRecord
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    fixed_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    dsr = _make_search_result()
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
    state["started_at"] = fixed_start
    state["run_id"] = "test-run"
    state["report"] = "report content"
    state["search_results_by_dimension"] = {"basic_info": dsr}
    state["summaries_by_dimension"] = {"basic_info": summary}
    state["cost"] = CostRecord()

    save_node(state)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["started_at"].startswith("2026-01-01")


def test_save_node_started_at_none_uses_fallback(tmp_path: Path) -> None:
    """save_node must not crash and must produce valid run_meta when started_at is None."""
    from diligence.models import CostRecord
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    dsr = _make_search_result()
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
    state["started_at"] = None  # sentinel: init_node failed
    state["run_id"] = "test-run"
    state["report"] = "report content"
    state["search_results_by_dimension"] = {"basic_info": dsr}
    state["summaries_by_dimension"] = {"basic_info": summary}
    state["cost"] = CostRecord()

    save_node(state)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["started_at"] is not None  # fallback datetime was used
    assert meta["finished_at"] is not None


# -- _format_summaries --


def test_format_summaries_skipped_dims() -> None:
    """Skipped dimensions appear as 未执行 sections."""
    from diligence.nodes.merge_node import _format_summaries

    summaries: dict[str, DimensionSummary] = {
        "basic_info": DimensionSummary(
            dimension_id="basic_info",
            dimension_name="工商基本信息",
            status="success",
            summary="测试摘要",
            confidence="高",
            uncertain_facts=[],
            evidence_item_ids=[],
        ),
    }
    skipped = [{"id": "ip", "name": "知识产权"}, {"id": "listing", "name": "上市情况"}]

    result = _format_summaries(summaries, ["basic_info"], skipped_dims=skipped)

    assert "未执行" in result
    assert "知识产权" in result
    assert "上市情况" in result
    assert "本维度未在本次运行中检索" in result


def test_format_summaries_missing_from_summaries() -> None:
    """Active dimension missing from summaries shows 执行失败."""
    from diligence.nodes.merge_node import _format_summaries

    summaries: dict[str, DimensionSummary] = {}
    active_dim_names = {"basic_info": "工商基本信息"}

    result = _format_summaries(
        summaries,
        ["basic_info"],
        active_dim_names=active_dim_names,
    )

    assert "执行失败" in result
    assert "工商基本信息" in result
    assert "搜索或抓取过程异常" in result


def test_format_summaries_all_active_no_skipped() -> None:
    """Normal case: all active dims have summaries, no skipped dims."""
    from diligence.nodes.merge_node import _format_summaries

    summaries: dict[str, DimensionSummary] = {
        "basic_info": DimensionSummary(
            dimension_id="basic_info",
            dimension_name="工商基本信息",
            status="success",
            summary="正常摘要",
            confidence="中",
            uncertain_facts=["待核实项"],
            evidence_item_ids=["id1"],
        ),
    }

    result = _format_summaries(summaries, ["basic_info"])

    assert "正常摘要" in result
    assert "待核实项" in result
    assert "未执行" not in result
    assert "执行失败" not in result


# -- save_node edge cases --


def test_save_node_missing_active_dimension_counts_as_failed(tmp_path: Path) -> None:
    """Active dimension absent from summaries is tracked as failed in run_meta."""
    from diligence.models import CostRecord
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    state = _base_state(cfg, tmp_path)
    state["run_id"] = "test-run"
    state["report"] = "partial report"
    state["search_results_by_dimension"] = {}
    state["summaries_by_dimension"] = {}  # basic_info is active but missing
    state["cost"] = CostRecord()

    save_node(state)
    meta = json.loads((tmp_path / "run_meta.json").read_text())

    assert meta["status"] == "partial"
    assert meta["required_failed"] is True
    assert "basic_info" in meta["failed_dimensions"]


async def test_search_node_cross_provider_dedup(tmp_path: Path) -> None:
    """MiniMax + Metaso source items with same URL -> dedup keeps only Metaso."""
    from diligence.nodes.search_node import search_node

    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["{target} 工商"],
                metaso_queries=["{target}"],
                metaso_mode="chat",
                summary_prompt="请分析{target}。\n{results}",
            )
        ],
    )

    shared_url = "https://example.com/company/shared"
    minimax_organic = [
        {"title": "MiniMax标题", "link": shared_url, "snippet": "s", "date": ""},
    ]
    mm_mock = MagicMock()
    mm_mock.__aenter__ = AsyncMock(return_value=mm_mock)
    mm_mock.__aexit__ = AsyncMock(return_value=False)
    mm_resp = MagicMock(spec=httpx.Response)
    mm_resp.json.return_value = {"organic": minimax_organic}
    mm_resp.raise_for_status = MagicMock()
    mm_mock.post = AsyncMock(return_value=mm_resp)

    from diligence.models import make_item_id as mii
    from diligence.utils.metaso import make_metaso_source_items

    fake_answer = SearchItem(
        id=mii(url="metaso://search?q=x", title="a", snippet="a"),
        title="a",
        url="metaso://search?q=x",
        snippet="a",
        query="q",
        dimension_id="basic_info",
        source="metaso_chat",
        fetched_at=datetime.now(UTC),
    )
    fake_sources = make_metaso_source_items(
        [{"title": "dup", "link": shared_url, "summary": "dup"}],
        "q",
        "basic_info",
    )

    state = _base_state(cfg, tmp_path)
    state["current_dimension"] = cfg.dimensions[0]

    with (
        patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mm_mock),
        patch("diligence.nodes.search_node.enrich_with_metaso") as mock_metaso,
        patch("diligence.nodes.search_node.settings") as mock_settings,
    ):
        mock_settings.metaso_enabled = True
        mock_settings.metaso_api_key = True
        mock_settings.metaso_verify_tls = True
        mock_metaso.return_value = ([*fake_sources, fake_answer], 1, 0, 6)
        result = await search_node(state)

    dsr = result["search_results_by_dimension"]["basic_info"]
    urls = [item.url for item in dsr.items if item.url == shared_url]
    assert len(urls) == 1, f"Expected 1 item with shared URL after dedup, got {len(urls)}"
    kept = next(item for item in dsr.items if item.url == shared_url)
    assert kept.source == "metaso_chat"
