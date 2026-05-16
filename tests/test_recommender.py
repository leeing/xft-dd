from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from diligence.recommender.config_loader import load_dimensions_config, load_products_config
from diligence.recommender.dimension_analyzer import analyze_dimensions
from diligence.recommender.graph import run_recommendation
from diligence.recommender.models import AnalysisDimension, EvidenceTemplate
from diligence.warehouse.prophet_loader import load_prophet_data


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_recommender_configs(tmp_path: Path) -> tuple[Path, Path]:
    dimensions = {
        "version": "1.0",
        "dimensions": [
            {
                "id": "supply_chain_procurement",
                "level1": "供应链与采购管理",
                "level2": "采购规模与特征",
                "level3": "供应链复杂度",
                "role": "供应链管理与商业调研专家",
                "local_fields": ["industry", "employee_count"],
                "evidence_templates": [
                    {"field": "industry", "label": "行业"},
                    {"field": "employee_count", "label": "员工规模"},
                    {"field": "bidding_total", "label": "招投标数量"},
                ],
                "insufficient_evidence": ["供应商数量"],
            },
            {
                "id": "hr_workforce",
                "level1": "人力资源与用工特征",
                "level2": "用工与考勤特征",
                "level3": "用工规模",
                "role": "企业运营与人力资源分析师",
                "local_fields": ["employee_count"],
                "evidence_templates": [{"field": "employee_count", "label": "员工规模"}],
                "insufficient_evidence": ["倒班制度"],
            },
        ],
    }
    products = {
        "version": "1.0",
        "scenario": "product_recommendation",
        "output_dir": str(tmp_path / "runs"),
        "products": [
            {
                "module_id": "procurement_srm",
                "module_name": "供应商关系管理(SRM)",
                "priority": 90,
                "target_needs": ["supply_chain_procurement"],
                "match_rule": "制造业且有采购协同需求",
            },
            {
                "module_id": "hr_attendance",
                "module_name": "人力资源与考勤管理",
                "priority": 80,
                "target_needs": ["hr_workforce"],
                "match_rule": "员工规模较大",
            },
        ],
    }
    dimensions_path = tmp_path / "analysis_dimensions.yaml"
    products_path = tmp_path / "products.yaml"
    dimensions_path.write_text(yaml.safe_dump(dimensions, allow_unicode=True), encoding="utf-8")
    products_path.write_text(yaml.safe_dump(products, allow_unicode=True), encoding="utf-8")
    return products_path, dimensions_path


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
                        "cate2": "化学制品",
                        "businessScope": "精细化学品生产销售",
                        "listedCompanyState": 1,
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


def test_recommender_configs_load() -> None:
    products = load_products_config("config/recommender/products.yaml")
    dimensions = load_dimensions_config("config/recommender/analysis_dimensions.yaml")

    assert products.products
    assert dimensions.dimensions
    assert {item.module_id for item in products.products}
    assert {item.id for item in dimensions.dimensions}


def test_dimension_analyzer_marks_supported_and_missing() -> None:
    dim = AnalysisDimension(
        id="supply_chain_procurement",
        level1="供应链与采购管理",
        level2="采购规模与特征",
        level3="供应链复杂度",
        role="供应链管理与商业调研专家",
        evidence_templates=[
            EvidenceTemplate(field="industry", label="行业"),
            EvidenceTemplate(field="employee_count", label="员工规模"),
            EvidenceTemplate(field="bidding_total", label="招投标数量"),
        ],
        insufficient_evidence=["供应商数量"],
    )

    result = analyze_dimensions(
        profile={"industry": "制造业", "employee_count": 300, "bidding_total": 4},
        dimensions=[dim],
    )[0]

    assert result.status == "supported"
    assert result.facts
    assert "供应商数量" in result.missing_evidence


@pytest.mark.asyncio
async def test_run_recommendation_mvp_without_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("diligence.settings.settings.llm_api_key", "")
    monkeypatch.setattr("diligence.settings.settings.minimax_api_key", "")
    warehouse = _build_warehouse(tmp_path)
    products_path, dimensions_path = _write_recommender_configs(tmp_path)

    result = await run_recommendation(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        products_config_path=str(products_path),
        dimensions_config_path=str(dimensions_path),
        output_dir=str(tmp_path / "runs"),
        run_id="test-run",
    )

    assert result.status in ("success", "partial")
    output_dir = Path(result.output_dir)
    assert (output_dir / "profile.json").exists()
    assert (output_dir / "dimension_analysis.json").exists()
    assert (output_dir / "match_results.json").exists()
    assert (output_dir / "result.json").exists()
    assert (output_dir / "report.md").exists()
    payload = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["recommendations"]

