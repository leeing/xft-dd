from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch

from xft.runtime.batch import PipelineBatchRequest, run_pipeline_batch
from xft.runtime.models import PipelineRunRequest, PipelineRunResult


async def test_runtime_batch_writes_common_artifacts(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    import xft.runtime.batch as runtime_batch

    async def fake_run_pipeline(request: PipelineRunRequest) -> PipelineRunResult:
        out = Path(str(request.output_dir))
        if request.pipeline == "recommender":
            out = out / str(request.run_id)
        out.mkdir(parents=True, exist_ok=True)
        return PipelineRunResult(
            pipeline=request.pipeline,
            target=request.target,
            status="success",
            run_id=str(request.run_id),
            output_dir=str(out),
            report_path=str(out / "report.md"),
            result_path=str(out / "result.json"),
        )

    monkeypatch.setattr(runtime_batch, "run_pipeline", fake_run_pipeline)

    result = await run_pipeline_batch(
        PipelineBatchRequest(
            pipeline="recommender",
            targets=["公司A", "公司B"],
            batch_id="runtime-batch",
            batch_output=str(tmp_path),
            use_llm=False,
        )
    )

    batch_dir = Path(result.batch_dir)
    assert result.status == "success"
    assert (batch_dir / "batch_manifest.json").exists()
    assert (batch_dir / "batch_summary.json").exists()
    assert (batch_dir / "batch_summary.csv").exists()
    assert (batch_dir / "batch_quality_report.json").exists()
    assert (batch_dir / "delivery_manifest.json").exists()
    rows = json.loads((batch_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert [row["company_name"] for row in rows] == ["公司A", "公司B"]
    delivery = json.loads((batch_dir / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert any(item["type"] == "company_report" for item in delivery["files"])


async def test_runtime_batch_records_failure_and_stop(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    import xft.runtime.batch as runtime_batch

    async def fake_run_pipeline(request: PipelineRunRequest) -> PipelineRunResult:
        if request.target == "失败公司":
            msg = "boom"
            raise RuntimeError(msg)
        return PipelineRunResult(
            pipeline=request.pipeline,
            target=request.target,
            status="success",
            run_id=str(request.run_id),
            output_dir=str(tmp_path / "ok"),
        )

    monkeypatch.setattr(runtime_batch, "run_pipeline", fake_run_pipeline)

    result = await run_pipeline_batch(
        PipelineBatchRequest(
            pipeline="diligence",
            targets=["失败公司", "不会运行"],
            batch_id="runtime-fail",
            batch_output=str(tmp_path),
            continue_on_error=False,
        )
    )

    assert result.status == "failed"
    assert len(result.rows) == 1
    assert result.rows[0]["status"] == "failed"
    assert Path(result.failed_companies_path).read_text(encoding="utf-8") == "失败公司\n"
