from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from diligence.pipeline.recommender.config_loader import load_dimensions_config, load_products_config
from diligence.pipeline.recommender.dimension_analyzer import analyze_dimensions
from diligence.pipeline.recommender.graph import run_recommendation
from diligence.pipeline.recommender.models import AnalysisDimension, EvidenceTemplate, MatchResult, ProductModule
from diligence.pipeline.recommender.recommendation_normalizer import normalize_recommendation_payload
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
                "analysis_prompt": "判断采购协同需求。",
                "evidence_policy": "制造业和规模只能作为间接线索。",
                "support_rules": [
                    {
                        "field": "employee_count",
                        "op": ">=",
                        "value": 200,
                        "claim": "员工规模较大，可能存在采购流程协同需求。",
                        "confidence": "低",
                    }
                ],
                "web_search_queries": ["{company_name} 供应商", "{company_name} 招投标"],
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


def test_recommender_config_loader_supports_bundle_directory(tmp_path: Path) -> None:
    products_path, dimensions_path = _write_recommender_configs(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "products.yaml").write_text(products_path.read_text(encoding="utf-8"), encoding="utf-8")
    dimensions_dir = bundle / "dimensions"
    dimensions_dir.mkdir()
    raw_dimensions = yaml.safe_load(dimensions_path.read_text(encoding="utf-8"))["dimensions"]
    for item in raw_dimensions:
        (dimensions_dir / f"{item['id']}.yaml").write_text(
            yaml.safe_dump(item, allow_unicode=True),
            encoding="utf-8",
        )

    products = load_products_config(bundle)
    dimensions = load_dimensions_config(bundle)

    assert [item.module_id for item in products.products] == ["procurement_srm", "hr_attendance"]
    assert {item.id for item in dimensions.dimensions} == {"supply_chain_procurement", "hr_workforce"}


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


def test_dimension_analyzer_applies_configured_support_rules() -> None:
    dim = AnalysisDimension(
        id="supply_chain_procurement",
        level1="供应链与采购管理",
        level2="采购规模与特征",
        level3="供应链复杂度",
        role="供应链管理与商业调研专家",
        evidence_templates=[EvidenceTemplate(field="industry", label="行业")],
        support_rules=[
            {
                "field": "employee_count",
                "op": ">=",
                "value": 200,
                "claim": "员工规模较大，可能存在采购流程协同需求。",
                "confidence": "低",
            },
            {
                "field": "labels",
                "op": "contains",
                "value": "高新技术",
                "claim": "标签包含高新技术线索。",
                "confidence": "低",
            },
        ],
        analysis_prompt="判断采购管理复杂度。",
        evidence_policy="不得把制造业直接等同于采购规模大。",
        web_search_queries=["{company_name} 供应商", "{company_name} {industry} 采购"],
        insufficient_evidence=["供应商数量"],
    )

    result = analyze_dimensions(
        profile={
            "company_name": "测试公司",
            "industry": "制造业",
            "employee_count": 300,
            "labels": ["高新技术企业"],
        },
        dimensions=[dim],
    )[0]

    assert result.analysis_prompt == "判断采购管理复杂度。"
    assert result.evidence_policy == "不得把制造业直接等同于采购规模大。"
    assert result.web_search_queries == ["测试公司 供应商", "测试公司 制造业 采购"]
    assert "员工规模较大，可能存在采购流程协同需求。" in result.inferences
    assert "标签包含高新技术线索。" in result.inferences


def test_dimension_analyzer_uses_chinese_labels_for_risk_counts() -> None:
    dim = AnalysisDimension(
        id="compliance_risk",
        level1="合规与风险评估",
        level2="法律风险排查",
        level3="风险与合规信号",
        role="复合型商业尽调专家团队",
        evidence_templates=[EvidenceTemplate(field="risk_counts", label="风险计数")],
        insufficient_evidence=["关联交易风险"],
    )

    result = analyze_dimensions(
        profile={"risk_counts": {"self": 301, "pre": 57, "court_session": 193, "judgement_doc": 87}},
        dimensions=[dim],
    )[0]

    claim = result.facts[0].claim
    assert "自身风险:301" in claim
    assert "历史变更风险:57" in claim
    assert "开庭公告:193" in claim
    assert "裁判文书:87" in claim
    assert "court_session" not in claim


def test_recommendation_normalizer_repairs_llm_payload() -> None:
    products = [
        ProductModule(
            module_id="finance_erp",
            module_name="财务管理(ERP)",
            priority=86,
            target_needs=["finance_tax"],
            match_rule="财务复杂度较高",
        ),
        ProductModule(
            module_id="hr_attendance",
            module_name="人力资源与考勤管理",
            priority=80,
            target_needs=["hr_workforce"],
            match_rule="员工规模较大",
        ),
    ]
    matches = [
        MatchResult(
            module_id="finance_erp",
            module_name="财务管理(ERP)",
            matched=True,
            score=70,
            confidence="中",
            business_need="财务核算与业财协同",
            reason="多组织财务管理复杂",
            supporting_dimensions=["finance_tax"],
            missing_evidence=["财务系统使用情况"],
        ),
        MatchResult(
            module_id="hr_attendance",
            module_name="人力资源与考勤管理",
            matched=True,
            score=60,
            confidence="中",
            business_need="人事考勤管理",
            reason="员工规模较大",
            supporting_dimensions=["hr_workforce"],
            missing_evidence=["排班制度"],
        ),
    ]

    output = normalize_recommendation_payload(
        {
            "summary": "",
            "recommendations": [
                {"rank": 9, "module_id": "unknown", "module_name": "非法模块", "score": 99},
                {"rank": 2, "module_id": "hr_attendance", "module_name": "", "score": -5},
                {"rank": 1, "module_id": "finance_erp", "module_name": "财务管理(ERP)", "score": 120},
                {"rank": 3, "module_id": "finance_erp", "score": 90},
            ],
        },
        company_name="测试公司",
        scenario="product_recommendation",
        products=products,
        match_results=matches,
        needs_web_enrichment=True,
        profile_completeness=1.2,
    )

    assert [item.module_id for item in output.recommendations] == ["finance_erp", "hr_attendance"]
    assert [item.rank for item in output.recommendations] == [1, 2]
    assert output.recommendations[0].score == 100
    assert output.recommendations[1].score == 0
    assert output.recommendations[1].module_name == "人力资源与考勤管理"
    assert output.profile_completeness == 1.0


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
    assert payload["evidence_summary"]["local_evidence_count"] > 0
    assert "by_dimension" in payload["evidence_summary"]
    first_rec = payload["recommendations"][0]
    assert first_rec["score_breakdown"]["final_score"] == first_rec["score"]
    assert "matched_rules" in first_rec["score_breakdown"]
    assert "scoring_summary" in payload
    assert payload["scoring_summary"]["rules_evaluated"] >= 0
    assert first_rec["evidence_trace"]
    assert {"evidence_id", "claim", "source_type", "relation_to_profile"} <= set(first_rec["evidence_trace"][0])
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "推荐评分总览" in report
    assert "分数构成" in report
    dimensions = json.loads((output_dir / "dimension_analysis.json").read_text(encoding="utf-8"))
    assert dimensions[0]["analysis_prompt"] == "判断采购协同需求。"
    assert dimensions[0]["evidence_policy"] == "制造业和规模只能作为间接线索。"
    assert dimensions[0]["web_search_queries"] == [
        "广东德美精细化工集团股份有限公司 供应商",
        "广东德美精细化工集团股份有限公司 招投标",
    ]
