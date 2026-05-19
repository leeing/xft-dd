"""Evaluate business-facing recommendation labels with rule + LLM indicators."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from time import perf_counter
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.pipeline.recommender.business_models import (
    BusinessConfidence,
    BusinessIndicatorConfig,
    BusinessIndicatorResult,
    BusinessLabelConfig,
    BusinessLabelResult,
    BusinessModuleConfig,
    BusinessModuleResult,
    BusinessRecommendationConfig,
    BusinessRecommendationResult,
    BusinessResult,
)
from xft.pipeline.recommender.models import DimensionAnalysis
from xft.progress import display
from xft.settings import settings

LLM_TIMEOUT_SECONDS = 45
MAX_EVIDENCE_ITEMS = 24
RAW_PREVIEW_CHARS = 500


class _LlmIndicatorPayload(BaseModel):
    result: BusinessResult
    confidence: BusinessConfidence
    current_status: str
    evidence: list[str] = []


async def evaluate_business_recommendation(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig | None,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    use_llm: bool,
    llm_debug: bool = False,
    llm_concurrency: int = 4,
) -> BusinessRecommendationResult | None:
    """Evaluate the optional business recommendation layer."""
    if config is None:
        return None

    warnings: list[str] = []
    modules: list[BusinessModuleResult] = []
    all_indicators: list[BusinessIndicatorResult] = []
    concurrency = max(1, llm_concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    if llm_debug and use_llm and (settings.llm_api_key or settings.minimax_api_key):
        display.info(f"LLM 业务指标并发: {concurrency}")
    module_results = await asyncio.gather(
        *[
            _evaluate_module(
                config=config,
                module=module,
                company_name=company_name,
                profile=profile,
                dimension_analysis=dimension_analysis,
                use_llm=use_llm,
                llm_debug=llm_debug,
                semaphore=semaphore,
            )
            for module in config.modules
        ]
    )
    for module_result, module_warnings in module_results:
        warnings.extend(module_warnings)
        modules.append(module_result)
        for label in module_result.label_results:
            all_indicators.extend(label.indicator_results)

    selected = _select_module(modules)
    return BusinessRecommendationResult(
        company_name=str(profile.get("company_name") or company_name),
        selected_module=selected,
        modules=modules,
        indicator_results=all_indicators,
        warnings=warnings,
    )


async def _evaluate_module(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    use_llm: bool,
    llm_debug: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[BusinessModuleResult, list[str]]:
    warnings: list[str] = []
    labels: list[BusinessLabelResult] = []
    label_results = await asyncio.gather(
        *[
            _evaluate_label(
                config=config,
                module=module,
                label=label,
                company_name=company_name,
                profile=profile,
                dimension_analysis=dimension_analysis,
                use_llm=use_llm,
                llm_debug=llm_debug,
                semaphore=semaphore,
            )
            for label in module.labels
        ]
    )
    for label_result, label_warnings in label_results:
        warnings.extend(label_warnings)
        labels.append(label_result)

    attributes_number = sum(1 for item in labels if item.result == "matched")
    indicators_number = sum(item.matched_indicators for item in labels)
    acceptance_result, conclusion = _acceptance(config, attributes_number, indicators_number)
    score = module.base_score + sum(item.score for item in labels)
    return (
        BusinessModuleResult(
            module_id=module.module_id,
            module_name=module.module_name,
            score=score,
            acceptance_result=acceptance_result,
            attributes_number=attributes_number,
            indicators_number=indicators_number,
            conclusion=conclusion,
            label_results=labels,
        ),
        warnings,
    )


async def _evaluate_label(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    use_llm: bool,
    llm_debug: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[BusinessLabelResult, list[str]]:
    warnings: list[str] = []
    indicators: list[BusinessIndicatorResult] = []
    indicator_results = await asyncio.gather(
        *[
            _evaluate_indicator_with_fallback(
                config=config,
                module=module,
                label=label,
                indicator=indicator,
                company_name=company_name,
                profile=profile,
                dimension_analysis=dimension_analysis,
                use_llm=use_llm,
                llm_debug=llm_debug,
                semaphore=semaphore,
            )
            for indicator in label.indicators
        ]
    )
    for result, warning in indicator_results:
        if warning:
            warnings.append(warning)
        indicators.append(result)

    matched = sum(1 for item in indicators if item.result == "matched")
    possible = sum(1 for item in indicators if item.result == "possible")
    if matched >= label.min_matched_indicators:
        label_result: BusinessResult = "matched"
    elif possible or matched:
        label_result = "possible"
    elif all(item.result == "unknown" for item in indicators):
        label_result = "unknown"
    else:
        label_result = "not_matched"
    score = config.scoring.label_scores.score_for(label_result)
    return (
        BusinessLabelResult(
            module_id=module.module_id,
            module_name=module.module_name,
            label_id=label.label_id,
            label_name=label.label_name,
            result=label_result,
            matched_indicators=matched,
            possible_indicators=possible,
            score=score,
            key_indicator_verify=_key_indicator_verify(matched, possible),
            indicator_results=indicators,
        ),
        warnings,
    )


async def _evaluate_indicator_with_fallback(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    use_llm: bool,
    llm_debug: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[BusinessIndicatorResult, str | None]:
    try:
        result = await _evaluate_indicator(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            company_name=company_name,
            profile=profile,
            dimension_analysis=dimension_analysis,
            use_llm=use_llm,
            llm_debug=llm_debug,
            semaphore=semaphore,
        )
    except (
        OpenAIError,
        json.JSONDecodeError,
        ValidationError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        if llm_debug:
            display.fail(f"LLM 调用失败 [业务指标:{_indicator_key(module, label, indicator)}] {_exc_summary(exc)}")
        result = _fallback_indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            profile=profile,
        )
        return result, f"{_indicator_key(module, label, indicator)}: {_exc_summary(exc)}"
    else:
        return result, None


async def _evaluate_indicator(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    use_llm: bool,
    llm_debug: bool,
    semaphore: asyncio.Semaphore,
) -> BusinessIndicatorResult:
    if indicator.evaluator == "rule":
        return _evaluate_rule_indicator(config, module, label, indicator, profile)
    if use_llm and (settings.llm_api_key or settings.minimax_api_key):
        async with semaphore:
            return await _evaluate_llm_indicator(
                config=config,
                module=module,
                label=label,
                indicator=indicator,
                company_name=company_name,
                profile=profile,
                dimension_analysis=dimension_analysis,
                llm_debug=llm_debug,
            )
    return _fallback_indicator_result(config=config, module=module, label=label, indicator=indicator, profile=profile)


def _evaluate_rule_indicator(
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    profile: dict[str, Any],
) -> BusinessIndicatorResult:
    if indicator.rule is None:
        msg = f"missing rule config: {indicator.indicator_id}"
        raise ValueError(msg)
    value = _get_path(profile, indicator.rule.source_field)
    matched = _compare(value, indicator.rule.op, indicator.rule.value)
    result: BusinessResult = "matched" if matched else "not_matched"
    evidence = [f"{indicator.rule.source_field} = {_display_value(value)}"] if value not in (None, "", [], {}) else []
    current_status = _rule_current_status(indicator, value, matched=matched)
    return _indicator_result(
        config=config,
        module=module,
        label=label,
        indicator=indicator,
        result=result,
        confidence="高" if matched else "中",
        current_status=current_status,
        evidence=evidence,
    )


async def _evaluate_llm_indicator(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    dimension_analysis: list[DimensionAnalysis],
    llm_debug: bool,
) -> BusinessIndicatorResult:
    key = _indicator_key(module, label, indicator)
    payload = {
        "company_name": company_name,
        "company_profile": _compact_profile(profile),
        "dimension_evidence": _dimension_evidence(dimension_analysis),
        "module": {"id": module.module_id, "name": module.module_name},
        "label": {"id": label.label_id, "name": label.label_name, "description": label.description},
        "indicator": {
            "id": indicator.indicator_id,
            "name": indicator.indicator_name,
            "standard": indicator.standard,
            "prompt": indicator.prompt,
            "evidence_hints": indicator.evidence_hints,
        },
    }
    system = (
        "你是企业业务属性识别专家。只能基于输入证据判断，不得编造。"
        "输出必须是 JSON，字段为 result、confidence、current_status、evidence。"
        "result 只能是 matched、possible、not_matched、unknown；confidence 只能是 高、中、低。"
        "证据不足时输出 unknown。current_status 要能直接给业务人员阅读。"
    )
    client = get_ai_client()
    if llm_debug:
        display.info(
            f"LLM 调用开始 [业务指标:{key}] model={settings.llm_model}, timeout={LLM_TIMEOUT_SECONDS}s"
        )
    started = perf_counter()
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        temperature=0.0,
        timeout=LLM_TIMEOUT_SECONDS,
    )
    raw = resp.choices[0].message.content or "{}"
    parsed = _LlmIndicatorPayload.model_validate(json.loads(extract_json(raw)))
    if llm_debug:
        display.info(
            f"LLM 调用完成 [业务指标:{key}] {perf_counter() - started:.2f}s, "
            f"result={parsed.result}, confidence={parsed.confidence}, raw={_preview(raw)}"
        )
    return _indicator_result(
        config=config,
        module=module,
        label=label,
        indicator=indicator,
        result=parsed.result,
        confidence=parsed.confidence,
        current_status=parsed.current_status,
        evidence=parsed.evidence,
    )


def _indicator_key(
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
) -> str:
    return f"{module.module_id}.{label.label_id}.{indicator.indicator_id}"


def _preview(text: str, limit: int = RAW_PREVIEW_CHARS) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _exc_summary(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text[:180]}" if text else type(exc).__name__


def _fallback_indicator_result(
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    profile: dict[str, Any],
) -> BusinessIndicatorResult:
    evidence = _hint_evidence(profile, indicator.evidence_hints)
    if evidence:
        result: BusinessResult = "matched"
        confidence: BusinessConfidence = "中"
        current_status = "；".join(evidence[:2])
    else:
        result = "unknown"
        confidence = "低"
        current_status = "现有本地证据不足，需进一步核实。"
    return _indicator_result(
        config=config,
        module=module,
        label=label,
        indicator=indicator,
        result=result,
        confidence=confidence,
        current_status=current_status,
        evidence=evidence,
    )


def _indicator_result(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    result: BusinessResult,
    confidence: BusinessConfidence,
    current_status: str,
    evidence: list[str],
) -> BusinessIndicatorResult:
    return BusinessIndicatorResult(
        module_id=module.module_id,
        module_name=module.module_name,
        label_id=label.label_id,
        label_name=label.label_name,
        indicator_id=indicator.indicator_id,
        indicator_name=indicator.indicator_name,
        result=result,
        confidence=confidence,
        score=config.scoring.indicator_scores.score_for(result),
        current_status=current_status,
        standard=indicator.standard,
        evidence=evidence[:8],
        evaluator=indicator.evaluator,
    )


def _acceptance(
    config: BusinessRecommendationConfig,
    attributes_number: int,
    indicators_number: int,
) -> tuple[str, str]:
    levels = sorted(config.acceptance_policy.levels, key=lambda item: item.min_matched_labels, reverse=True)
    selected = next((item for item in levels if attributes_number >= item.min_matched_labels), levels[-1])
    conclusion = selected.conclusion.format(
        attributes_number=attributes_number,
        indicators_number=indicators_number,
    )
    return selected.result, conclusion


def _select_module(modules: list[BusinessModuleResult]) -> BusinessModuleResult | None:
    if not modules:
        return None
    return sorted(modules, key=lambda item: (-item.score, -item.attributes_number, item.module_id))[0]


def _key_indicator_verify(matched: int, possible: int) -> str:
    if matched:
        return f"满足{matched}个指标"
    if possible:
        return f"可能满足{possible}个指标"
    return "证据不足"


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _compare(value: Any, op: str, expected: Any) -> bool:  # noqa: PLR0911
    if op == "exists":
        return value not in (None, "", [], {})
    if op == "contains":
        return _contains(value, expected)
    if op == "contains_any":
        values = expected if isinstance(expected, list) else [expected]
        return any(_contains(value, item) for item in values)
    if op in ("==", "!="):
        result = _normalize(value) == _normalize(expected)
        return result if op == "==" else not result
    left = _as_number(value)
    right = _as_number(expected)
    if left is None or right is None:
        return False
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def _contains(value: Any, expected: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return str(expected) in value
    if isinstance(value, dict):
        return _contains(list(value.values()), expected)
    if isinstance(value, Iterable):
        return any(str(expected) in str(item) for item in value)
    return str(expected) in str(value)


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "是", "1"):
            return True
        if lowered in ("false", "否", "0"):
            return False
        return lowered
    return value


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _rule_current_status(indicator: BusinessIndicatorConfig, value: Any, *, matched: bool) -> str:
    if matched:
        return f"{indicator.indicator_name}满足：{_display_value(value)}"
    if value in (None, "", [], {}):
        return f"未发现{indicator.indicator_name}的明确本地证据。"
    return f"{indicator.indicator_name}未满足：{_display_value(value)}"


def _display_value(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict | list) else str(value)
    return text[:180]


def _hint_evidence(profile: dict[str, Any], hints: list[str]) -> list[str]:
    if not hints:
        return []
    evidence: list[str] = []
    for key, value in profile.items():
        text = _display_value(value)
        for hint in hints:
            if hint and hint in text:
                evidence.append(f"{key} 包含“{hint}”")
                break
    return evidence[:8]


def _compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "company_name",
        "credit_code",
        "industry",
        "industry_big",
        "industry_mid",
        "industry_small",
        "business_scope",
        "employee_count",
        "branch_count",
        "labels",
        "ip_counts",
        "qualification_count",
        "recent_recruitment_titles",
        "recruitment_count",
        "cross_border_flags",
        "website",
    ]
    return {key: profile.get(key) for key in keys if key in profile}


def _dimension_evidence(dimension_analysis: list[DimensionAnalysis]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for analysis in dimension_analysis:
        evidence = [
            *analysis.local_evidence,
            *analysis.web_evidence,
            *analysis.inference_evidence,
        ]
        items.append(
            {
                "dimension_id": analysis.dimension_id,
                "title": analysis.title,
                "status": analysis.status,
                "confidence": analysis.confidence,
                "facts": [fact.claim for fact in analysis.facts[:5]],
                "evidence": [item.claim for item in evidence[:MAX_EVIDENCE_ITEMS]],
                "missing_evidence": analysis.missing_evidence[:8],
            }
        )
    return items
