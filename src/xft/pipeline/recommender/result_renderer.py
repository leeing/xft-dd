"""Render business-facing result.json from business recommendation results."""

from __future__ import annotations

from typing import Any

from xft.pipeline.recommender.models import (
    ModuleConfig,
    ModuleResult,
    RecommendationConfig,
    RecommendationResult,
)
from xft.utils.misc import result_text


def render_result_json(
    *,
    profile: dict[str, Any],
    result: RecommendationResult | None,
    config: RecommendationConfig | None,
) -> dict[str, Any]:
    """Render the stable business result JSON shape."""
    company_name = str(profile.get("company_name") or result.company_name if result else "")
    if result is None or result.selected_module is None or config is None:
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
            "Modules": [],
        }

    selected = result.selected_module
    module_config = _module_config(config, selected.module_id)
    matched_labels = _matched_labels(selected)
    modules = sorted(result.modules, key=lambda item: (-item.score, -item.attributes_number, item.module_id))
    return {
        "Acceptance": _acceptance_items(selected),
        "USCI": str(profile.get("credit_code") or ""),
        "LabelResult": _label_result_items(selected),
        "CoreBusinessAreas": _core_business_areas(profile),
        "AttributesNumber": selected.attributes_number,
        "CompanyName": str(profile.get("company_name") or result.company_name),
        "IndicatorsNumber": selected.indicators_number,
        "MarketingPoint": _marketing_points(module_config, matched_labels),
        "Module": selected.module_name,
        "Conclusion": selected.conclusion,
        "AcceptanceResult": selected.acceptance_result,
        "Modules": [_module_result_json(module=module, config=config) for module in modules],
    }


def _module_config(config: RecommendationConfig, module_id: str) -> ModuleConfig | None:
    return next((item for item in config.modules if item.module_id == module_id), None)


def _module_result_json(*, module: ModuleResult, config: RecommendationConfig) -> dict[str, Any]:
    module_config = _module_config(config, module.module_id)
    matched_labels = _matched_labels(module)
    return {
        "ModuleId": module.module_id,
        "Module": module.module_name,
        "Score": module.score,
        "AcceptanceResult": module.acceptance_result,
        "Conclusion": module.conclusion,
        "AttributesNumber": module.attributes_number,
        "IndicatorsNumber": module.indicators_number,
        "Acceptance": _acceptance_items(module),
        "LabelResult": _label_result_items(module),
        "MarketingPoint": _marketing_points(module_config, matched_labels),
    }


def _matched_labels(module: ModuleResult) -> list[Any]:
    return [item for item in module.label_results if item.result == "matched"]


def _matched_indicators(module: ModuleResult) -> list[Any]:
    return [
        indicator
        for label in module.label_results
        for indicator in label.indicator_results
        if indicator.result == "matched"
    ]


def _acceptance_items(module: ModuleResult) -> list[dict[str, Any]]:
    return [
        {
            "AcceptanceDetermination": "满足",
            "LabelType": label.label_name,
            "KeyIndicatorVerify": label.key_indicator_verify,
        }
        for label in _matched_labels(module)
    ]


def _label_result_items(module: ModuleResult) -> list[dict[str, Any]]:
    return [
        {
            "QuantitativeStandard": indicator.standard,
            "CurrentStatus": indicator.current_status,
            "ProfileName": indicator.indicator_name,
            "AnalysisResults": result_text(indicator.result),
            "LabelType": indicator.label_name,
        }
        for indicator in _matched_indicators(module)
    ]


def _marketing_points(module: ModuleConfig | None, matched_labels: list[Any]) -> list[dict[str, Any]]:
    if module is None:
        return []
    points: list[dict[str, Any]] = []
    for label in matched_labels:
        point = module.marketing_points.get(label.label_id)
        if point is None:
            continue
        indicators = [item.indicator_name for item in label.indicator_results if item.result == "matched"]
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
