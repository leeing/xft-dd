from __future__ import annotations

from xft.runtime.calibration import build_calibration_report, render_calibration_report


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

