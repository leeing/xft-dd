from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xft.pipeline.recommender.batch import BatchOptions, run_recommendation_batch
from xft.pipeline.recommender.models import RecommendationRunResult


async def _fake_runner(**kwargs: Any) -> RecommendationRunResult:
    company_name = str(kwargs["company_name"])
    run_id = str(kwargs["run_id"])
    output_dir = Path(str(kwargs["output_dir"])) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "profile.json").write_text(
        json.dumps({"profile_completeness": 0.8}, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "company_name": company_name,
                "scenario": "sales_recommendation",
                "scenario_name": "销售产品推荐",
                "recommendations": [{"module_id": "procurement_srm", "module_name": "SRM", "score": 78}],
                "profile_completeness": 0.8,
                "needs_web_enrichment": False,
                "evidence_summary": {
                    "local_evidence_count": 3,
                    "web_evidence_count": 1,
                    "conflict_count": 0,
                    "missing_evidence_count": 2,
                },
                "scoring_summary": {
                    "rules_evaluated": 5,
                    "rules_matched": 2,
                    "products_excluded": 0,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text("# report", encoding="utf-8")
    return RecommendationRunResult(
        company_name=company_name,
        status="success",
        run_id=run_id,
        output_dir=str(output_dir),
        report_path=str(output_dir / "report.md"),
        result_path=str(output_dir / "result.json"),
    )


async def _failing_runner(**kwargs: Any) -> RecommendationRunResult:
    if kwargs["company_name"] == "失败公司":
        msg = "boom"
        raise RuntimeError(msg)
    return await _fake_runner(**kwargs)


@pytest.mark.asyncio
async def test_recommendation_batch_writes_delivery_artifacts(tmp_path: Path) -> None:
    result = await run_recommendation_batch(
        company_names=["公司A", "公司B"],
        batch_id="batch-test",
        batch_output=str(tmp_path / "batches"),
        options=BatchOptions(warehouse_db="warehouse.duckdb", use_llm=False),
        runner=_fake_runner,
    )

    batch_dir = Path(result.batch_dir)
    assert result.status == "success"
    assert (batch_dir / "batch_manifest.json").exists()
    assert (batch_dir / "batch_summary.json").exists()
    assert (batch_dir / "batch_summary.csv").exists()
    assert (batch_dir / "batch_quality_report.json").exists()
    assert (batch_dir / "batch_quality_report.md").exists()
    assert (batch_dir / "delivery_manifest.json").exists()
    rows = json.loads((batch_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0]["scenario"] == "sales_recommendation"
    assert rows[0]["top_module_id"] == "procurement_srm"
    quality = json.loads((batch_dir / "batch_quality_report.json").read_text(encoding="utf-8"))
    assert quality["success_count"] == 2
    delivery = json.loads((batch_dir / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert any(item["type"] == "company_report" for item in delivery["files"])


@pytest.mark.asyncio
async def test_recommendation_batch_records_failures_and_limit(tmp_path: Path) -> None:
    result = await run_recommendation_batch(
        company_names=["公司A", "失败公司", "公司C"],
        batch_id="batch-fail",
        batch_output=str(tmp_path / "batches"),
        limit=2,
        options=BatchOptions(warehouse_db="warehouse.duckdb", use_llm=False),
        runner=_failing_runner,
    )

    batch_dir = Path(result.batch_dir)
    assert result.status == "partial"
    rows = json.loads((batch_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert [row["company_name"] for row in rows] == ["公司A", "失败公司"]
    assert rows[1]["status"] == "failed"
    assert (batch_dir / "failed_companies.txt").read_text(encoding="utf-8") == "失败公司\n"


@pytest.mark.asyncio
async def test_recommendation_batch_skip_existing(tmp_path: Path) -> None:
    options = BatchOptions(warehouse_db="warehouse.duckdb", use_llm=False)
    first = await run_recommendation_batch(
        company_names=["公司A"],
        batch_id="batch-skip",
        batch_output=str(tmp_path / "batches"),
        options=options,
        runner=_fake_runner,
    )
    second = await run_recommendation_batch(
        company_names=["公司A"],
        batch_id="batch-skip",
        batch_output=str(tmp_path / "batches"),
        options=options,
        skip_existing=True,
        runner=_failing_runner,
    )

    assert Path(first.batch_dir) == Path(second.batch_dir)
    rows = json.loads((Path(second.batch_dir) / "batch_summary.json").read_text(encoding="utf-8"))
    assert rows[0]["status"] == "skipped"
