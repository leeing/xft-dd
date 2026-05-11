"""Additional tests for init_node, collect_node, save_node, and config not covered elsewhere."""

from __future__ import annotations

import json
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from diligence.config import AppConfig, Dimension, load_config
from diligence.models import CostRecord, DimensionSearchResult, DimensionSummary, SearchItem, make_item_id
from diligence.state import DiligenceState


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_cfg(*, extra_dims: list[Dimension] | None = None) -> AppConfig:
    dims = [
        Dimension(
            id="basic_info",
            name="工商基本信息",
            order=10,
            enabled=True,
            required=True,
            minimax_queries=["{target} 工商注册"],
            summary_prompt="请分析{target}的工商信息。\n{results}",
        )
    ]
    if extra_dims:
        dims.extend(extra_dims)
    return AppConfig(
        merge_prompt="请综合{summaries}生成{target}的报告",
        dimensions=dims,
    )


def _make_item(url: str | None, i: int = 0) -> SearchItem:
    return SearchItem(
        id=make_item_id(url=url, title=f"t{i}", snippet=f"s{i}"),
        title=f"t{i}",
        url=url,
        snippet=f"s{i}",
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
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
        cost=CostRecord(),
    )


# ── init_node ─────────────────────────────────────────────────────────────────


def test_make_run_id_format() -> None:
    """run_id follows YYYYMMDD-HHMMSS-{6hex} format."""
    from diligence.nodes.init_node import make_run_id

    run_id = make_run_id("某公司")
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{6}$", run_id)


def test_make_run_id_deterministic_hash() -> None:
    """Same target produces the same hash suffix (only timestamp differs)."""
    from diligence.nodes.init_node import make_run_id

    id1 = make_run_id("固定公司名")
    id2 = make_run_id("固定公司名")
    # hash suffix (last 6 chars) must be identical
    assert id1[-6:] == id2[-6:]


def test_make_run_id_different_targets_different_hash() -> None:
    """Different targets produce different hash suffixes."""
    from diligence.nodes.init_node import make_run_id

    assert make_run_id("公司A")[-6:] != make_run_id("公司B")[-6:]


def test_init_node_filters_disabled_dimensions(tmp_path: Path) -> None:
    """Disabled dimensions are excluded from active_dimensions."""
    from diligence.nodes.init_node import init_node

    disabled = Dimension(
        id="industry",
        name="行业",
        order=20,
        enabled=False,
        required=False,
        minimax_queries=["{target} 行业"],
        summary_prompt="x\n{results}",
    )
    cfg = _make_cfg(extra_dims=[disabled])
    state = _base_state(cfg, tmp_path)
    result = init_node(state)
    active_ids = [d.id for d in result["active_dimensions"]]
    assert "basic_info" in active_ids
    assert "industry" not in active_ids


def test_init_node_creates_output_dir(tmp_path: Path) -> None:
    """init_node creates the output directory on disk."""
    from diligence.nodes.init_node import init_node

    cfg = _make_cfg()
    state = _base_state(cfg, tmp_path)
    state["output_dir"] = str(tmp_path / "nested" / "run-dir")
    result = init_node(state)
    assert Path(result["output_dir"]).exists()


# ── collect_node ──────────────────────────────────────────────────────────────


def test_collect_node_missing_dimension_produces_error(tmp_path: Path) -> None:
    """A dimension present in active_dimensions but absent from summaries triggers an error."""
    from diligence.nodes.collect_node import collect_node

    extra = Dimension(
        id="industry",
        name="行业",
        order=20,
        enabled=True,
        required=False,
        minimax_queries=["{target} 行业"],
        summary_prompt="x\n{results}",
    )
    cfg = _make_cfg(extra_dims=[extra])
    state = _base_state(cfg, tmp_path)
    # Only provide summary for basic_info, not industry
    state["active_dimensions"] = cfg.dimensions
    state["summaries_by_dimension"] = {
        "basic_info": DimensionSummary(
            dimension_id="basic_info",
            dimension_name="工商基本信息",
            status="success",
            summary="ok",
            confidence="中",
            uncertain_facts=[],
            evidence_item_ids=[],
        )
    }
    state["current_dimension"] = None
    result = collect_node(state)
    errors = result.get("errors", [])
    assert any("industry" in e.dimension_id for e in errors)


def test_collect_node_partial_on_required_dimension_is_flagged(tmp_path: Path) -> None:
    """A 'partial' status on a required dimension IS treated as a required-fail error."""
    from diligence.nodes.collect_node import collect_node

    cfg = _make_cfg()
    partial_summary = DimensionSummary(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status="partial",  # partial on a required dim → flagged
        summary="部分信息",
        confidence="低",
        uncertain_facts=[],
        evidence_item_ids=[],
    )
    state = _base_state(cfg, tmp_path)
    state["summaries_by_dimension"] = {"basic_info": partial_summary}
    state["current_dimension"] = None
    result = collect_node(state)
    required_errors = [e for e in result.get("errors", []) if "required" in e.message.lower() or "核心" in e.message]
    assert len(required_errors) == 1
    assert "partial" in required_errors[0].message.lower()


# ── save_node ─────────────────────────────────────────────────────────────────


def _make_save_state(cfg: AppConfig, tmp_path: Path, *, status: str = "success") -> DiligenceState:
    dsr = DimensionSearchResult(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status=status,  # type: ignore[arg-type]
        items=[_make_item("https://qcc.com/1")],
    )
    summary = DimensionSummary(
        dimension_id="basic_info",
        dimension_name="工商基本信息",
        status=status,  # type: ignore[arg-type]
        summary="摘要",
        confidence="中",
        uncertain_facts=[],
        evidence_item_ids=[],
    )
    state = _base_state(cfg, tmp_path)
    state["report"] = "# 报告内容"
    state["search_results_by_dimension"] = {"basic_info": dsr}
    state["summaries_by_dimension"] = {"basic_info": summary}
    state["cost"] = CostRecord()
    return state


def test_save_node_creates_all_four_artifacts(tmp_path: Path) -> None:
    """save_node writes all four required artifact files."""
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    state = _make_save_state(cfg, tmp_path)
    save_node(state)

    assert (tmp_path / "final_report.md").exists()
    assert (tmp_path / "raw_search_results.json").exists()
    assert (tmp_path / "dimension_summaries.json").exists()
    assert (tmp_path / "run_meta.json").exists()


def test_save_node_run_meta_status_success(tmp_path: Path) -> None:
    """run_meta.json contains status=success when no dimensions failed."""
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    state = _make_save_state(cfg, tmp_path, status="success")
    save_node(state)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["status"] == "success"
    assert meta["required_failed"] is False


def test_save_node_run_meta_status_partial_on_failed_dim(tmp_path: Path) -> None:
    """run_meta.json contains status=partial when a non-required dim fails but report exists."""
    from diligence.nodes.save_node import save_node

    extra = Dimension(
        id="industry",
        name="行业",
        order=20,
        enabled=True,
        required=False,
        minimax_queries=["{target} 行业"],
        summary_prompt="x\n{results}",
    )
    cfg = _make_cfg(extra_dims=[extra])
    state = _make_save_state(cfg, tmp_path, status="success")
    # Add a failed non-required dimension summary
    state["active_dimensions"] = cfg.dimensions
    state["summaries_by_dimension"]["industry"] = DimensionSummary(
        dimension_id="industry",
        dimension_name="行业",
        status="failed",
        summary="failed",
        confidence="待核实",
        uncertain_facts=[],
        evidence_item_ids=[],
        error="search failed",
    )
    state["search_results_by_dimension"]["industry"] = DimensionSearchResult(
        dimension_id="industry",
        dimension_name="行业",
        status="failed",
        items=[],
    )
    save_node(state)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["status"] == "partial"
    assert "industry" in meta["failed_dimensions"]
    assert meta["required_failed"] is False


def test_save_node_metaso_cost_printed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """When metaso_calls > 0, the Metaso cost line appears in stderr."""
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    state = _make_save_state(cfg, tmp_path)
    state["cost"] = CostRecord(metaso_calls=5, metaso_credits_total=15)
    save_node(state)
    err = capsys.readouterr().err
    assert "Metaso" in err
    assert "5" in err
    assert "15" in err


def test_save_node_no_metaso_line_when_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """When metaso_calls == 0, the Metaso line is NOT printed."""
    from diligence.nodes.save_node import save_node

    cfg = _make_cfg()
    state = _make_save_state(cfg, tmp_path)
    state["cost"] = CostRecord()
    save_node(state)
    err = capsys.readouterr().err
    assert "Metaso" not in err


# ── config.yaml: fetchable_domains ────────────────────────────────────────────


def test_config_fetchable_domains_loaded(tmp_path: Path) -> None:
    """fetchable_domains list is correctly loaded from config.yaml."""
    content = textwrap.dedent("""
        schema_version: "1.0"
        merge_prompt: "x"
        fetchable_domains:
          - "qcc.com"
          - "cnipa.gov.cn"
        dimensions:
          - id: basic_info
            name: 工商基本信息
            order: 10
            enabled: true
            required: true
            minimax_queries: ["{target} 工商"]
            summary_prompt: "{target}\\n{results}"
    """)
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = load_config(str(p))
    assert cfg.fetchable_domains == ["qcc.com", "cnipa.gov.cn"]


def test_config_fetchable_domains_defaults_to_empty(tmp_path: Path) -> None:
    """fetchable_domains defaults to [] when not specified."""
    content = textwrap.dedent("""
        schema_version: "1.0"
        merge_prompt: "x"
        dimensions:
          - id: basic_info
            name: 工商基本信息
            order: 10
            enabled: true
            required: true
            minimax_queries: ["{target} 工商"]
            summary_prompt: "{target}\\n{results}"
    """)
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = load_config(str(p))
    assert cfg.fetchable_domains == []
