"""Render business-facing result.json from business recommendation results."""

from __future__ import annotations

from typing import Any

from xft.pipeline.recommender.business_models import (
    BusinessModuleConfig,
    BusinessRecommendationConfig,
    BusinessRecommendationResult,
)


def render_business_result_json(
    *,
    profile: dict[str, Any],
    business_result: BusinessRecommendationResult | None,
    config: BusinessRecommendationConfig | None,
) -> dict[str, Any]:
    """Render the stable business result JSON shape."""
    company_name = str(profile.get("company_name") or business_result.company_name if business_result else "")
    if business_result is None or business_result.selected_module is None or config is None:
        return {
            "Acceptance": [],
            "USCI": str(profile.get("credit_code") or ""),
            "LabelResult": [],
            "CoreBusinessAreas": _core_business_areas(profile),
            "AttributesNumber": 0,
            "CompanyName": company_name,
            "IndicatorsNumber": 0,
            "MarketingPoint": [],
            "Module": "",
            "Conclusion": "未生成业务推荐结果。",
            "AcceptanceResult": "低",
        }

    selected = business_result.selected_module
    module_config = _module_config(config, selected.module_id)
    matched_labels = [item for item in selected.label_results if item.result == "matched"]
    matched_indicators = [
        indicator
        for label in selected.label_results
        for indicator in label.indicator_results
        if indicator.result == "matched"
    ]
    return {
        "Acceptance": [
            {
                "AcceptanceDetermination": "满足",
                "LabelType": label.label_name,
                "KeyIndicatorVerify": label.key_indicator_verify,
            }
            for label in matched_labels
        ],
        "USCI": str(profile.get("credit_code") or ""),
        "LabelResult": [
            {
                "QuantitativeStandard": indicator.standard,
                "CurrentStatus": indicator.current_status,
                "ProfileName": indicator.indicator_name,
                "AnalysisResults": _business_result_text(indicator.result),
                "LabelType": indicator.label_name,
            }
            for indicator in matched_indicators
        ],
        "CoreBusinessAreas": _core_business_areas(profile),
        "AttributesNumber": selected.attributes_number,
        "CompanyName": str(profile.get("company_name") or business_result.company_name),
        "IndicatorsNumber": selected.indicators_number,
        "MarketingPoint": _marketing_points(module_config, matched_labels),
        "Module": selected.module_name,
        "Conclusion": selected.conclusion,
        "AcceptanceResult": selected.acceptance_result,
    }


def _module_config(config: BusinessRecommendationConfig, module_id: str) -> BusinessModuleConfig | None:
    return next((item for item in config.modules if item.module_id == module_id), None)


def _marketing_points(module: BusinessModuleConfig | None, matched_labels: list[Any]) -> list[dict[str, Any]]:
    if module is None:
        return []
    points: list[dict[str, Any]] = []
    for label in matched_labels:
        point = module.marketing_points.get(label.label_id)
        if point is None:
            continue
        indicators = [
            item.indicator_name
            for item in label.indicator_results
            if item.result == "matched"
        ]
        points.append(
            {
                "Recommendation": point.recommendation.strip(),
                "LabelType": label.label_name,
                "KeyIndicatorVerify": f"命中{'、'.join(indicators)}。" if indicators else label.key_indicator_verify,
                "SaleRule": point.sale_rule.strip(),
                "KycKeyPoints": point.kyc_questions,
            }
        )
    return points


def _core_business_areas(profile: dict[str, Any]) -> str:
    parts = [
        str(profile.get("industry_mid") or "").strip(),
        str(profile.get("industry_small") or "").strip(),
    ]
    text = " / ".join(part for part in parts if part)
    if text:
        return text
    return str(profile.get("industry_big") or profile.get("industry") or "")


def _business_result_text(result: str) -> str:
    return {
        "matched": "满足",
        "possible": "可能满足",
        "not_matched": "不满足",
        "unknown": "证据不足",
    }.get(result, result)
