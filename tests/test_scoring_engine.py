from __future__ import annotations

from diligence.evidence.models import EvidenceRecord
from diligence.pipeline.recommender.models import DimensionAnalysis, ProductModule
from diligence.scoring.models import ScoringContext
from diligence.scoring.score_engine import score_products


def test_score_engine_applies_positive_negative_and_exclusion_rules() -> None:
    analysis = DimensionAnalysis(
        dimension_id="supply_chain_procurement",
        title="供应链与采购管理",
        status="supported",
        confidence="中",
        local_evidence=[
            EvidenceRecord(
                evidence_id="local_1",
                company_name="测试公司",
                dimension_id="supply_chain_procurement",
                source_type="local_json",
                source_name="company_profile",
                source_field="bidding_total",
                claim="招投标数量：5",
                confidence="中",
                relation_to_profile="primary",
            )
        ],
        missing_evidence=["供应商数量"],
        conflicts=[
            EvidenceRecord(
                evidence_id="web_conflict_1",
                company_name="测试公司",
                dimension_id="supply_chain_procurement",
                source_type="web",
                source_name="web",
                claim="Web 声称企业无招投标记录",
                confidence="低",
                relation_to_profile="conflict",
                conflict_note="本地画像显示招投标数量为 5",
                resolution="use_local",
            )
        ],
    )
    product = ProductModule(
        module_id="procurement_srm",
        module_name="供应商关系管理(SRM)",
        priority=90,
        base_score=50,
        target_needs=["supply_chain_procurement"],
        match_rule="采购协同",
        positive_rules=[
            {
                "id": "bidding_signal",
                "source_field": "bidding_total",
                "op": ">",
                "value": 0,
                "weight": 10,
                "reason": "存在招投标记录",
            }
        ],
        negative_rules=[
            {
                "id": "missing_supplier_count",
                "missing_evidence": "供应商数量",
                "penalty": 5,
                "reason": "缺少供应商数量",
            }
        ],
        exclusion_rules=[
            {
                "id": "inactive_company",
                "source_field": "reg_status",
                "op": "!=",
                "value": "存续",
                "reason": "企业状态非存续",
            }
        ],
    )

    result = score_products(
        products=[product],
        context=ScoringContext(
            company_profile={"bidding_total": 5, "reg_status": "吊销"},
            dimension_analyses=[analysis],
        ),
    )

    scored = result.product_scores[0]
    assert scored.excluded is True
    assert scored.final_score == 20
    assert scored.score_breakdown.positive_score == 10
    assert scored.score_breakdown.negative_score == -5
    assert scored.score_breakdown.matched_rules[0].rule_id == "bidding_signal"
    assert scored.score_breakdown.penalty_rules[0].rule_id == "missing_supplier_count"
    assert scored.score_breakdown.exclusion_rules[0].rule_id == "inactive_company"
    assert result.summary.rules_evaluated == 3
    assert result.summary.rules_matched == 3
    assert result.summary.products_excluded == 1
