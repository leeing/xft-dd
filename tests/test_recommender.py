from __future__ import annotations

import json
from pathlib import Path

import pytest

from xft.pipeline.recommender.graph import run_recommendation
from xft.warehouse.prophet_loader import load_prophet_data


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _build_warehouse(tmp_path: Path) -> Path:
    input_root = tmp_path / "data"
    company = input_root / "91440606707539050R_广东德美精细化工集团股份有限公司"
    _write_json(
        company / ".meta.json",
        {"company_name": "广东德美精细化工集团股份有限公司", "credit_code": "91440606707539050R", "fetchers": {}},
    )
    _write_json(
        company / "info.json",
        {
            "data": {
                "info": {
                    "info": {
                        "name": "广东德美精细化工集团股份有限公司",
                        "unifiedSocialCreditCode": "91440606707539050R",
                        "cate1": "制造业",
                        "businessScope": "精细化学品生产销售",
                    }
                }
            }
        },
    )
    _write_json(
        company / "query_company.json",
        {"data": {"entName": "广东德美精细化工集团股份有限公司", "employeeNum": 1779, "idtCtgNm": "制造业"}},
    )
    _write_json(company / "label.json", {"labels": ["高质量客户"], "raw_label_codes": ["high_value.png"]})
    _write_json(company / "intellectual.json", {"data": {"intellectual": [{"name": "专利查询", "messageNo": 3}]}})
    _write_json(company / "risk_insight.json", {"data": {"riskCount": {"selfRisk": 2, "preRisk": 1}}})
    _write_json(company / "recruit_message.json", {"data": {"list": [{"title": "生产主管"}]}})
    _write_json(company / "query_bidding_total.json", {"data": {"total": 5}})
    _write_json(company / "query_qualification.json", {"data": [{"labNm": "高新技术企业认定"}]})
    _write_json(company / "staff.json", {"data": {"list": [{"name": "黄冠雄", "staffTypeName": "董事长"}]}})
    _write_json(company / "shareholder.json", {"data": {"list": [{"investorName": "股东A"}]}})
    db = tmp_path / "warehouse.duckdb"
    load_prophet_data(input_root=input_root, output_db=db)
    return db


@pytest.mark.asyncio
async def test_run_recommendation_business_first_without_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("xft.settings.settings.llm_api_key", "")
    monkeypatch.setattr("xft.settings.settings.minimax_api_key", "")
    warehouse = _build_warehouse(tmp_path)

    result = await run_recommendation(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        scenario_path="config/recommend/sales_recommendation",
        output_dir=str(tmp_path / "runs"),
        run_id="test-run",
        use_llm=False,
    )

    assert result.status in ("success", "partial")
    output_dir = Path(result.output_dir)
    assert (output_dir / "profile.json").exists()
    assert not (output_dir / "dimension_analysis.json").exists()
    assert not (output_dir / "match_results.json").exists()
    assert not (output_dir / "internal_result.json").exists()
    assert (output_dir / "label_result.json").exists()
    assert (output_dir / "indicator_evidence.json").exists()
    assert (output_dir / "result.json").exists()
    assert (output_dir / "report.md").exists()
    payload = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert "Module" in payload
    assert "AcceptanceResult" in payload
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "推荐模块总览" in report
    assert "业务推荐结果" in report
    assert "维度分析摘要" not in report
