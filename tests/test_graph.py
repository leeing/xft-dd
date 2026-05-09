from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from diligence.config import AppConfig, Dimension


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


async def test_run_company_graph_produces_artifacts(tmp_path: Path) -> None:
    """Full pipeline: run_company_graph saves all 4 artifact files."""
    from diligence.graph import run_company_graph

    cfg = _make_cfg()
    output_dir = str(tmp_path / "run_001")

    mmx_output = json.dumps({"organic": [
        {"title": "A", "link": "https://qcc.com/1", "snippet": "注册资本100万"},
    ]}).encode()
    ai_summary = json.dumps({
        "summary": "某公司成立于2020年",
        "confidence": "中",
        "uncertain_facts": [],
        "evidence_item_ids": [],
    })
    ai_report = "# 企业尽调报告：某公司\n\n## 一、工商基本信息\n**可信度：中**\n某公司成立于2020年"

    async def fake_exec(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(mmx_output, b""))
        return proc

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(side_effect=[
        MagicMock(choices=[MagicMock(message=MagicMock(content=ai_summary))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=ai_report))]),
    ])

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_client):
            with patch("diligence.nodes.merge_node.get_ai_client", return_value=mock_client):
                result = await run_company_graph(target="某公司", config=cfg, output_dir=output_dir)

    assert result.status in ("success", "partial")
    out = Path(output_dir)
    assert (out / "final_report.md").exists()
    assert (out / "raw_search_results.json").exists()
    assert (out / "dimension_summaries.json").exists()
    assert (out / "run_meta.json").exists()


async def test_run_company_graph_run_id_unique(tmp_path: Path) -> None:
    """Two sequential runs of the same target produce different run_ids."""
    from diligence.graph import run_company_graph

    cfg = _make_cfg()
    mmx_output = json.dumps({"organic": []}).encode()
    ai_output = json.dumps({"summary": "s", "confidence": "待核实",
                             "uncertain_facts": [], "evidence_item_ids": []})
    ai_report = "报告内容"

    def make_mock() -> MagicMock:
        m = MagicMock()
        m.chat.completions.create = MagicMock(side_effect=[
            MagicMock(choices=[MagicMock(message=MagicMock(content=ai_output))]),
            MagicMock(choices=[MagicMock(message=MagicMock(content=ai_report))]),
        ])
        return m

    async def fake_exec(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(mmx_output, b""))
        return proc

    results = []
    for i in range(2):
        out_dir = str(tmp_path / f"run_{i:03d}")
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            with patch("diligence.nodes.summarize_node.get_ai_client", return_value=make_mock()):
                with patch("diligence.nodes.merge_node.get_ai_client", return_value=make_mock()):
                    r = await run_company_graph("某公司", cfg, out_dir)
                    results.append(r)
        await asyncio.sleep(1)

    assert results[0].run_id != results[1].run_id


async def test_run_company_graph_required_fail_sets_flag(tmp_path: Path) -> None:
    """When basic_info (required=True) search fails entirely, required_failed is True."""
    from diligence.graph import run_company_graph

    cfg = _make_cfg()

    async def failing_exec(*args, **kwargs):
        proc = MagicMock()
        async def boom():
            raise RuntimeError("mmx down")
        proc.communicate = boom
        return proc

    ai_report = "报告内容"
    ai_fallback = json.dumps({"summary": "s", "confidence": "待核实",
                               "uncertain_facts": [], "evidence_item_ids": []})

    mock_sum = MagicMock()
    mock_sum.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_fallback))])
    )
    mock_merge = MagicMock()
    mock_merge.chat.completions.create = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=ai_report))])
    )

    with patch("asyncio.create_subprocess_exec", side_effect=failing_exec):
        with patch("diligence.nodes.summarize_node.get_ai_client", return_value=mock_sum):
            with patch("diligence.nodes.merge_node.get_ai_client", return_value=mock_merge):
                result = await run_company_graph("某公司", cfg, str(tmp_path / "run_fail"))

    assert result.required_failed is True
