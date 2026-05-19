from __future__ import annotations

from xft.pipeline.recommender.business_config_loader import load_business_recommendation_config
from xft.pipeline.recommender.business_evaluator import evaluate_business_recommendation
from xft.pipeline.recommender.business_result_renderer import render_business_result_json


async def test_business_recommendation_no_llm_generates_result_json_shape() -> None:
    config = load_business_recommendation_config("config/scenarios/sales_recommendation")
    assert config is not None

    profile = {
        "company_name": "广东泰琪丰电子有限公司",
        "credit_code": "91440000MA5UW5Y08T",
        "industry": "制造业",
        "industry_mid": "电子设备制造",
        "industry_small": "小家电制造",
        "business_scope": "电子设备、小家电研发、生产、销售、维修服务",
        "labels": ["高新技术企业认定", "科技企业资质4星"],
        "ip_counts": {"patent": 12, "software": 0},
        "recent_recruitment_titles": ["区域销售经理", "渠道业务员", "售后维修工程师"],
    }

    result = await evaluate_business_recommendation(
        config=config,
        company_name="广东泰琪丰电子有限公司",
        profile=profile,
        dimension_analysis=[],
        use_llm=False,
    )
    assert result is not None
    assert result.selected_module is not None
    assert result.selected_module.module_id == "daily_reimbursement"
    assert result.selected_module.attributes_number == 3
    assert result.selected_module.indicators_number >= 5
    tech_cert = next(item for item in result.indicator_results if item.indicator_id == "tech_certification")
    assert tech_cert.evaluator == "hybrid"
    assert tech_cert.result == "matched"
    assert tech_cert.hybrid_trace["merge_policy"] == "rule_first"
    assert tech_cert.hybrid_trace["final_decision"] == "rule matched, skipped llm"

    payload = render_business_result_json(profile=profile, business_result=result, config=config)

    assert payload["CompanyName"] == "广东泰琪丰电子有限公司"
    assert payload["USCI"] == "91440000MA5UW5Y08T"
    assert payload["Module"] == "日常报销"
    assert payload["AcceptanceResult"] == "高"
    assert payload["AttributesNumber"] == 3
    assert payload["LabelResult"]
    assert payload["MarketingPoint"]


def test_business_config_loader_accepts_scenario_bundle() -> None:
    config = load_business_recommendation_config("config/scenarios/sales_recommendation")

    assert config is not None
    module_ids = {module.module_id for module in config.modules}
    assert module_ids == {
        "attendance",
        "corporate_payment",
        "daily_reimbursement",
        "input_invoice",
        "output_invoice",
        "personal_tax",
        "travel_reimbursement",
    }
    daily = next(module for module in config.modules if module.module_id == "daily_reimbursement")
    assert daily.labels[0].label_name == "产销一体属性"
    tech_cert = next(
        ind for label in daily.labels for ind in label.indicators if ind.indicator_id == "tech_certification"
    )
    assert tech_cert.evaluator == "hybrid"
    assert tech_cert.merge_policy == "rule_first"
