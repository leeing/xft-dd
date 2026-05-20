from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from xft.runtime.calibration import (
    CalibrationLabel,
    build_calibration_report,
    load_calibration_labels,
    render_calibration_report,
    run_recommendation_calibration,
    write_web_review_samples,
)


def test_build_calibration_report_detects_distribution_and_quality_issues() -> None:
    rows = [
        {
            "company_name": f"公司{i}",
            "status": "success",
            "top_module_id": "procurement_srm" if i < 4 else "crm_channel",
            "top_score": 55 if i == 0 else 70,
            "recommendation_count": 3,
            "profile_completeness": 0.5 if i == 1 else 0.9,
            "conflict_count": 4 if i == 2 else 0,
        }
        for i in range(5)
    ]
    rows.append(
        {
            "company_name": "无推荐公司",
            "status": "success",
            "top_module_id": "",
            "top_score": "",
            "recommendation_count": 0,
            "profile_completeness": 0.8,
            "conflict_count": 0,
        }
    )

    report = build_calibration_report("batch-cal", rows)

    assert report.company_count == 6
    assert report.top_module_distribution[0]["module_id"] == "procurement_srm"
    assert report.low_score_companies[0]["company_name"] == "公司0"
    assert report.low_completeness_companies[0]["company_name"] == "公司1"
    assert report.high_conflict_companies[0]["company_name"] == "公司2"
    assert report.no_recommendation_companies[0]["company_name"] == "无推荐公司"
    assert {issue.title for issue in report.issues} >= {
        "Top1 产品集中度偏高",
        "存在低画像完整度企业",
        "存在高冲突企业",
        "存在无推荐企业",
    }
    rendered = render_calibration_report(report)
    assert "推荐规则校准报告" in rendered
    assert "procurement_srm" in rendered


def test_load_calibration_labels_accepts_multiple_separators(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(
        "\n".join(
            [
                "company_name,expected_top_module,acceptable_modules,comment",
                '公司A,crm_channel,"finance_tax；erp_mrp|hr_attendance",备注A',
                '公司B,procurement_srm,"procurement_srm，erp_mrp",备注B',
                ",crm_channel,crm_channel,空公司名跳过",
            ]
        ),
        encoding="utf-8",
    )

    labels = load_calibration_labels(path)

    assert len(labels) == 2
    assert labels[0].company_name == "公司A"
    assert labels[0].acceptable_modules == ["crm_channel", "finance_tax", "erp_mrp", "hr_attendance"]
    assert labels[0].comment == "备注A"
    assert labels[1].acceptable_modules == ["procurement_srm", "erp_mrp"]


def test_build_calibration_report_with_business_labels() -> None:
    rows = [
        {
            "company_name": "公司A",
            "status": "success",
            "top_module_id": "crm_channel",
            "top_score": 88,
            "recommendation_count": 3,
            "profile_completeness": 0.9,
            "conflict_count": 0,
        },
        {
            "company_name": "公司B",
            "status": "success",
            "top_module_id": "finance_tax",
            "top_score": 82,
            "recommendation_count": 3,
            "profile_completeness": 0.9,
            "conflict_count": 0,
        },
        {
            "company_name": "公司C",
            "status": "success",
            "top_module_id": "hr_attendance",
            "top_score": 78,
            "recommendation_count": 3,
            "profile_completeness": 0.9,
            "conflict_count": 0,
        },
    ]
    labels = [
        CalibrationLabel(
            company_name="公司A",
            expected_top_module="crm_channel",
            acceptable_modules=["crm_channel"],
        ),
        CalibrationLabel(
            company_name="公司B",
            expected_top_module="crm_channel",
            acceptable_modules=["crm_channel", "finance_tax"],
        ),
        CalibrationLabel(
            company_name="公司C",
            expected_top_module="procurement_srm",
            acceptable_modules=["procurement_srm"],
        ),
        CalibrationLabel(
            company_name="未运行公司",
            expected_top_module="crm_channel",
            acceptable_modules=["crm_channel"],
        ),
    ]

    report = build_calibration_report("batch-labels", rows, labels=labels)

    assert report.labeled_count == 3
    assert report.top1_match_count == 1
    assert report.acceptable_match_count == 2
    assert report.top1_accuracy == 0.3333
    assert report.acceptable_accuracy == 0.6667
    assert report.label_mismatches == [
        {
            "company_name": "公司C",
            "actual_top_module": "hr_attendance",
            "expected_top_module": "procurement_srm",
            "acceptable_modules": ["procurement_srm"],
            "comment": "",
        }
    ]
    assert any(issue.title == "业务标注命中率偏低" for issue in report.issues)


def test_render_calibration_report_with_business_labels() -> None:
    report = build_calibration_report(
        "batch-labels",
        [
            {
                "company_name": "公司A",
                "status": "success",
                "top_module_id": "finance_tax",
                "top_score": 80,
                "recommendation_count": 2,
                "profile_completeness": 0.9,
                "conflict_count": 0,
            }
        ],
        labels=[
            CalibrationLabel(
                company_name="公司A",
                expected_top_module="crm_channel",
                acceptable_modules=["crm_channel"],
            )
        ],
    )

    rendered = render_calibration_report(report)

    assert "业务标注命中率" in rendered
    assert "Top1 命中率" in rendered
    assert "可接受命中率" in rendered
    assert "公司A：实际 finance_tax，期望 crm_channel" in rendered


def test_build_calibration_report_with_web_metrics() -> None:
    report = build_calibration_report(
        "web-cal",
        [
            {
                "company_name": "公司A",
                "status": "success",
                "top_module_id": "crm_channel",
                "top_score": 88,
                "recommendation_count": 3,
                "profile_completeness": 0.9,
                "conflict_count": 0,
                "web_evidence_count": 2,
                "web_search_executed": 3,
                "web_search_reused": 1,
                "web_fetch_executed": 2,
                "web_fetch_reused": 1,
                "web_extraction_executed": 2,
                "web_extraction_reused": 0,
            },
            {
                "company_name": "公司B",
                "status": "success",
                "top_module_id": "finance_tax",
                "top_score": 76,
                "recommendation_count": 3,
                "profile_completeness": 0.9,
                "conflict_count": 0,
                "web_evidence_count": 0,
                "web_search_executed": 0,
                "web_search_reused": 1,
                "web_fetch_executed": 0,
                "web_fetch_reused": 1,
                "web_extraction_executed": 0,
                "web_extraction_reused": 1,
            },
        ],
        use_llm=True,
        with_web=True,
    )

    assert report.use_llm is True
    assert report.with_web is True
    assert report.web_evidence_coverage == 0.5
    assert report.web_evidence_zero_companies[0]["company_name"] == "公司B"
    assert any(issue.title == "业务 Web 证据覆盖率偏低" for issue in report.issues)
    rendered = render_calibration_report(report)
    assert "业务 Web 校准" in rendered
    assert "业务 Web 证据覆盖率：50.0%" in rendered


def test_write_web_review_samples_reads_web_evidence_trace(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(
        """
        {
          "recommendations": [
            {
              "module_id": "crm_channel",
              "evidence_trace": [
                {
                  "source_type": "local_json",
                  "claim": "本地事实"
                },
                {
                  "source_type": "web",
                  "claim": "官网显示公司有经销网络",
                  "relation_to_profile": "supplement",
                  "source_url": "https://example.com"
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    out = tmp_path / "review.csv"

    write_web_review_samples(
        out,
        [
            {
                "company_name": "公司A",
                "status": "success",
                "top_module_id": "crm_channel",
                "top_score": 90,
                "web_evidence_count": 1,
                "conflict_count": 0,
                "result_path": str(result_path),
            }
        ],
    )

    text = out.read_text(encoding="utf-8")
    assert "sample_evidence_claim" in text
    assert "官网显示公司有经销网络" in text
    assert "https://example.com" in text


async def test_run_recommendation_calibration_passes_business_web_options(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class FakeBatch:
        batch_id = "web-cal"
        batch_dir = str(tmp_path / "web-cal")
        rows = [
            {
                "company_name": "公司A",
                "status": "success",
                "top_module_id": "crm_channel",
                "top_score": 90,
                "recommendation_count": 1,
                "profile_completeness": 0.9,
                "conflict_count": 0,
                "web_evidence_count": 1,
            }
        ]
        status = "success"

    async def fake_run_recommendation_batch(**kwargs: Any) -> FakeBatch:
        captured.update(kwargs)
        Path(FakeBatch.batch_dir).mkdir(parents=True, exist_ok=True)
        return FakeBatch()

    import xft.pipeline.recommender.batch as recommender_batch

    monkeypatch.setattr(recommender_batch, "run_recommendation_batch", fake_run_recommendation_batch)

    batch, report, json_path, md_path, review_path = await run_recommendation_calibration(
        company_names=["公司A"],
        warehouse_db="warehouse.duckdb",
        batch_output=str(tmp_path),
        use_llm=True,
        with_web=True,
        refresh_web=True,
        web_config_path="web.yaml",
        web_providers=["minimax_search"],
    )

    assert batch.status == "success"
    assert report.with_web is True
    assert json_path.exists()
    assert md_path.exists()
    assert review_path.exists()
    options = captured["options"]
    assert options.use_llm is True
    assert options.with_web is True
    assert options.refresh_web is True
    assert options.web_config_path == "web.yaml"
    assert options.web_providers == ["minimax_search"]
