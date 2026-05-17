from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xft.runtime.artifacts import (
    batch_status,
    build_quality_report,
    write_delivery_manifest,
    write_failed_companies,
    write_quality_report,
)


def test_diligence_batch_summary_writes_runtime_artifacts(tmp_path: Path) -> None:
    from xft.pipeline.diligence.batch import _write_summary
    from xft.pipeline.diligence.models import CompanyRunResult

    results = [
        CompanyRunResult(
            index=1,
            target="公司A",
            run_id="dd_a",
            status="success",
            report_path=str(tmp_path / "companies" / "001" / "final_report.md"),
            artifacts_dir=str(tmp_path / "companies" / "001"),
        ),
        CompanyRunResult(index=2, target="公司B", status="failed", error="boom"),
    ]

    _write_summary(
        tmp_path,
        "batch-dd",
        results,
        ["公司A", "公司B"],
        started_at=datetime.now(UTC),
        config_path="config",
        input_file="companies.txt",
    )

    assert (tmp_path / "batch_quality_report.json").exists()
    assert (tmp_path / "batch_quality_report.md").exists()
    assert (tmp_path / "delivery_manifest.json").exists()
    assert (tmp_path / "failed_companies.txt").read_text(encoding="utf-8") == "公司B\n"
    delivery = json.loads((tmp_path / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert any(item["type"] == "summary_md" for item in delivery["files"])
    assert any(item["type"] == "batch_errors" for item in delivery["files"])


def test_build_quality_report_common_metrics() -> None:
    rows = [
        {
            "company_name": "公司A",
            "status": "success",
            "top_module_id": "srm",
            "top_score": 80,
            "profile_completeness": 0.8,
            "conflict_count": 0,
        },
        {
            "company_name": "公司B",
            "status": "failed",
            "profile_completeness": 0.4,
            "conflict_count": 5,
            "error": "boom",
        },
    ]

    report = build_quality_report("batch-test", rows, pipeline="recommender")

    assert report.pipeline == "recommender"
    assert report.success_count == 1
    assert report.failed_count == 1
    assert report.average_profile_completeness == 0.6
    assert report.average_top_score == 80
    assert report.top_modules == [{"module_id": "srm", "count": 1}]
    assert report.high_conflict_companies[0]["company_name"] == "公司B"
    assert report.failed_companies[0]["error"] == "boom"
    assert batch_status(rows) == "partial"


def test_write_runtime_delivery_artifacts(tmp_path: Path) -> None:
    rows = [
        {
            "company_name": "公司A",
            "status": "success",
            "report_path": str(tmp_path / "report.md"),
            "result_path": str(tmp_path / "result.json"),
        },
        {"company_name": "公司B", "status": "failed", "error": "boom"},
    ]
    (tmp_path / "batch_manifest.json").write_text("{}", encoding="utf-8")

    failed = write_failed_companies(tmp_path, rows)
    quality_json, quality_md = write_quality_report(tmp_path, "batch-test", rows, pipeline="test")
    delivery = write_delivery_manifest(
        batch_dir=tmp_path,
        batch_id="batch-test",
        rows=rows,
        quality_json=quality_json,
        quality_md=quality_md,
        failed_path=failed,
    )

    assert failed.read_text(encoding="utf-8") == "公司B\n"
    assert quality_json.exists()
    assert quality_md.exists()
    payload = json.loads(delivery.read_text(encoding="utf-8"))
    assert any(item["type"] == "company_report" for item in payload["files"])
    assert any(item["type"] == "failed_companies" for item in payload["files"])
