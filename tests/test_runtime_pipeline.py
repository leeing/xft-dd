from __future__ import annotations

from pathlib import Path
from typing import cast

from pytest import MonkeyPatch

from xft.pipeline.diligence.config import AppConfig
from xft.runtime import PipelineRunRequest, PipelineRunResult, run_pipeline


def test_pipeline_request_defaults() -> None:
    request = PipelineRunRequest(pipeline="recommender", target="测试企业")

    assert request.warehouse_db == "cache/company_warehouse.duckdb"
    assert request.use_llm is True
    assert request.use_web is False
    assert request.options == {}


async def test_run_pipeline_dispatches_recommender(monkeypatch: MonkeyPatch) -> None:
    from xft.pipeline.recommender.models import RecommendationRunResult
    from xft.pipeline import recommender

    seen: dict[str, object] = {}

    async def fake_run_recommendation(**kwargs: object) -> RecommendationRunResult:
        seen.update(kwargs)
        return RecommendationRunResult(
            company_name=str(kwargs["company_name"]),
            status="success",
            run_id="rec_test",
            output_dir="out/rec_test",
            report_path="out/rec_test/report.md",
            result_path="out/rec_test/result.json",
        )

    monkeypatch.setattr(recommender, "run_recommendation", fake_run_recommendation)

    result = await run_pipeline(
        PipelineRunRequest(
            pipeline="recommender",
            target="广东测试有限公司",
            scenario_path="config/scenarios/sales_recommendation",
            use_web=True,
            options={"web_providers": ["minimax"], "web_force_dimensions": True},
        )
    )

    assert result == PipelineRunResult(
        pipeline="recommender",
        target="广东测试有限公司",
        status="success",
        run_id="rec_test",
        output_dir="out/rec_test",
        result_path="out/rec_test/result.json",
        report_path="out/rec_test/report.md",
        raw={
            "company_name": "广东测试有限公司",
            "status": "success",
            "run_id": "rec_test",
            "output_dir": "out/rec_test",
            "report_path": "out/rec_test/report.md",
            "result_path": "out/rec_test/result.json",
            "error": None,
        },
    )
    assert seen["company_name"] == "广东测试有限公司"
    assert seen["with_web"] is True
    assert seen["use_web_evidence"] is True
    assert seen["web_providers"] == ["minimax"]
    assert seen["web_force_dimensions"] is True


async def test_run_pipeline_dispatches_diligence(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from xft.pipeline.diligence.config import Dimension
    from xft.pipeline.diligence.models import CompanyRunResult
    import xft.pipeline.diligence.config as diligence_config
    import xft.pipeline.diligence.graph as diligence_graph
    from xft.pipeline.diligence.nodes import init_node

    config = AppConfig(
        merge_prompt="merge",
        dimensions=[
            Dimension(id="basic", name="基础信息", order=1, minimax_queries=["{target} 基础"], summary_prompt="sum"),
            Dimension(id="risk", name="风险", order=2, minimax_queries=["{target} 风险"], summary_prompt="sum"),
        ],
    )
    seen: dict[str, object] = {}

    def fake_load_config(_path: str) -> AppConfig:
        return config

    def fake_make_run_id(_target: str) -> str:
        return "dd_test"

    monkeypatch.setattr(diligence_config, "load_config", fake_load_config)
    monkeypatch.setattr(init_node, "make_run_id", fake_make_run_id)

    async def fake_run_company_graph(**kwargs: object) -> CompanyRunResult:
        seen.update(kwargs)
        return CompanyRunResult(
            index=0,
            target=str(kwargs["target"]),
            run_id=str(kwargs["run_id"]),
            status="success",
            report_path=str(tmp_path / "report.md"),
            artifacts_dir=str(tmp_path),
        )

    monkeypatch.setattr(diligence_graph, "run_company_graph", fake_run_company_graph)

    result = await run_pipeline(
        PipelineRunRequest(
            pipeline="diligence",
            target="广东测试有限公司",
            config_path="config",
            only_dimensions=["risk"],
        )
    )

    assert result.pipeline == "diligence"
    assert result.status == "success"
    assert result.run_id == "dd_test"
    assert result.output_dir == str(tmp_path)
    seen_config = cast(AppConfig, seen["config"])
    assert [dim.id for dim in seen_config.dimensions] == ["risk"]
