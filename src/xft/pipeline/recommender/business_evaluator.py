"""Evaluate business-facing recommendation labels with rule + LLM indicators."""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.ai.llm_trace import (
    exception_summary,
    llm_event,
    preview_text,
    print_llm_failure,
    print_llm_start,
    print_llm_success,
)
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
from xft.progress import display
from xft.settings import settings
from xft.utils.misc import contains, get_nested

LLM_TIMEOUT_SECONDS = 45
MAX_EVIDENCE_ITEMS = 24


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
    business_evidence: dict[str, list[dict[str, Any]]] | None = None,
    business_web_trace: list[dict[str, Any]] | None = None,
    use_llm: bool,
    llm_debug: bool = False,
    llm_concurrency: int = 4,
    llm_events: list[dict[str, Any]] | None = None,
) -> BusinessRecommendationResult | None:
    """Evaluate the optional business recommendation layer."""
    if config is None:
        return None

    warnings: list[str] = []
    modules: list[BusinessModuleResult] = []
    all_indicators: list[BusinessIndicatorResult] = []
    concurrency = max(1, llm_concurrency)
    semaphore = asyncio.Semaphore(concurrency)
    llm_events = llm_events if llm_events is not None else []
    business_evidence = business_evidence or {}
    web_trace_by_indicator = _web_trace_by_indicator(business_web_trace or [])
    if llm_debug and use_llm and (settings.llm_api_key or settings.minimax_api_key):
        display.info(f"LLM 业务指标并发: {concurrency}")
    module_results = await asyncio.gather(
        *[
            _evaluate_module(
                config=config,
                module=module,
                company_name=company_name,
                profile=profile,
                business_evidence=business_evidence,
                web_trace_by_indicator=web_trace_by_indicator,
                use_llm=use_llm,
                llm_debug=llm_debug,
                llm_events=llm_events,
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
    business_evidence: dict[str, list[dict[str, Any]]],
    web_trace_by_indicator: dict[str, list[dict[str, Any]]],
    use_llm: bool,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
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
                business_evidence=business_evidence,
                web_trace_by_indicator=web_trace_by_indicator,
                use_llm=use_llm,
                llm_debug=llm_debug,
                llm_events=llm_events,
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
    acceptance_result, conclusion = _acceptance(config, module, attributes_number, indicators_number)
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
    business_evidence: dict[str, list[dict[str, Any]]],
    web_trace_by_indicator: dict[str, list[dict[str, Any]]],
    use_llm: bool,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
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
                business_evidence=business_evidence,
                web_trace_by_indicator=web_trace_by_indicator,
                use_llm=use_llm,
                llm_debug=llm_debug,
                llm_events=llm_events,
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
    business_evidence: dict[str, list[dict[str, Any]]],
    web_trace_by_indicator: dict[str, list[dict[str, Any]]],
    use_llm: bool,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
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
            business_evidence=business_evidence,
            use_llm=use_llm,
            llm_debug=llm_debug,
            llm_events=llm_events,
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
        recorded = bool(getattr(exc, "_xft_llm_event_recorded", False))
        should_record_failure = (
            indicator.evaluator in ("llm", "hybrid", "llm_web")
            and use_llm
            and (settings.llm_api_key or settings.minimax_api_key)
            and not recorded
        )
        if should_record_failure:
            llm_events.append(
                llm_event(
                    stage="business_indicator",
                    name=_indicator_key(module, label, indicator),
                    model=settings.llm_model,
                    status="failed",
                    elapsed_seconds=0,
                    request={
                        "module_id": module.module_id,
                        "label_id": label.label_id,
                        "indicator_id": indicator.indicator_id,
                    },
                    parameters={"temperature": 0.0, "timeout_seconds": LLM_TIMEOUT_SECONDS},
                    error=exc,
                )
            )
        if llm_debug and not recorded:
            print_llm_failure(
                title=f"业务指标:{_indicator_key(module, label, indicator)}",
                elapsed_seconds=0,
                error=exc,
                fallback="本地 evidence_hints 兜底判断",
            )
        result = _fallback_indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            profile=profile,
            indicator_evidence=_indicator_evidence(business_evidence, module, label, indicator),
        )
        result = _with_actual_web_trace(result, web_trace_by_indicator, module, label, indicator)
        return result, f"{_indicator_key(module, label, indicator)}: {exception_summary(exc)}"
    else:
        return _with_actual_web_trace(result, web_trace_by_indicator, module, label, indicator), None


async def _evaluate_indicator(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    business_evidence: dict[str, list[dict[str, Any]]],
    use_llm: bool,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> BusinessIndicatorResult:
    indicator_evidence = _indicator_evidence(business_evidence, module, label, indicator)
    if indicator.evaluator == "rule":
        return _evaluate_rule_indicator(config, module, label, indicator, profile, indicator_evidence)
    if indicator.evaluator == "hybrid":
        return await _evaluate_hybrid_indicator(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            company_name=company_name,
            profile=profile,
            business_evidence=business_evidence,
            use_llm=use_llm,
            llm_debug=llm_debug,
            llm_events=llm_events,
            semaphore=semaphore,
        )
    if use_llm and (settings.llm_api_key or settings.minimax_api_key):
        async with semaphore:
            return await _evaluate_llm_indicator(
                config=config,
                module=module,
                label=label,
                indicator=indicator,
                company_name=company_name,
                profile=profile,
                indicator_evidence=indicator_evidence,
                llm_debug=llm_debug,
                llm_events=llm_events,
            )
    return _fallback_indicator_result(
        config=config,
        module=module,
        label=label,
        indicator=indicator,
        profile=profile,
        indicator_evidence=indicator_evidence,
    )


async def _evaluate_hybrid_indicator(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    business_evidence: dict[str, list[dict[str, Any]]],
    use_llm: bool,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> BusinessIndicatorResult:
    indicator_evidence = _indicator_evidence(business_evidence, module, label, indicator)
    rule_result = _evaluate_rule_indicator(config, module, label, indicator, profile, indicator_evidence)
    trace: dict[str, Any] = {
        "merge_policy": indicator.merge_policy,
        "rule_result": rule_result.result,
        "rule_confidence": rule_result.confidence,
        "rule_current_status": rule_result.current_status,
        "rule_evidence": rule_result.evidence,
        "llm_called": False,
        "llm_result": None,
        "final_decision": "",
    }
    if indicator.merge_policy == "rule_first" and rule_result.result == "matched":
        trace["final_decision"] = "rule matched, skipped llm"
        return rule_result.model_copy(update={"evaluator": "hybrid", "hybrid_trace": trace})
    if indicator.merge_policy == "require_both" and rule_result.result != "matched":
        trace["final_decision"] = "rule did not match, require_both failed without llm"
        return rule_result.model_copy(update={"evaluator": "hybrid", "hybrid_trace": trace})
    if not (use_llm and (settings.llm_api_key or settings.minimax_api_key)):
        fallback = _fallback_indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            profile=profile,
            indicator_evidence=indicator_evidence,
        )
        trace.update(
            {
                "llm_called": False,
                "fallback_result": fallback.result,
                "fallback_evidence": fallback.evidence,
                "final_decision": "llm unavailable, used rule result if matched else evidence_hints fallback",
            }
        )
        if rule_result.result == "matched":
            return rule_result.model_copy(update={"evaluator": "hybrid", "hybrid_trace": trace})
        return fallback.model_copy(update={"evaluator": "hybrid", "hybrid_trace": trace})
    async with semaphore:
        llm_result = await _evaluate_llm_indicator(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            company_name=company_name,
            profile=profile,
            indicator_evidence=indicator_evidence,
            llm_debug=llm_debug,
            llm_events=llm_events,
        )
    trace.update(
        {
            "llm_called": True,
            "llm_result": llm_result.result,
            "llm_confidence": llm_result.confidence,
            "llm_current_status": llm_result.current_status,
            "llm_evidence": llm_result.evidence,
        }
    )
    final = _merge_hybrid_result(rule_result, llm_result, policy=indicator.merge_policy)
    trace["final_decision"] = _hybrid_decision_text(rule_result.result, llm_result.result, indicator.merge_policy)
    return final.model_copy(
        update={
            "evaluator": "hybrid",
            "score": config.scoring.indicator_scores.score_for(final.result),
            "hybrid_trace": trace,
        }
    )


def _merge_hybrid_result(
    rule_result: BusinessIndicatorResult,
    llm_result: BusinessIndicatorResult,
    *,
    policy: str,
) -> BusinessIndicatorResult:
    if policy == "llm_confirm":
        if rule_result.result == "matched" and llm_result.result == "not_matched":
            result: BusinessResult = "possible"
            confidence: BusinessConfidence = "中"
        elif rule_result.result == "matched" and llm_result.result == "unknown":
            result = "possible"
            confidence = "中"
        else:
            result = llm_result.result
            confidence = llm_result.confidence
        return llm_result.model_copy(update={"result": result, "confidence": confidence})
    if policy == "require_both":
        if rule_result.result == "matched" and llm_result.result == "matched":
            return llm_result.model_copy(update={"result": "matched", "confidence": llm_result.confidence})
        if rule_result.result == "matched" or llm_result.result == "matched":
            return llm_result.model_copy(update={"result": "possible", "confidence": "中"})
        return llm_result.model_copy(update={"result": "not_matched", "confidence": "中"})
    if rule_result.result == "matched":
        return rule_result
    return llm_result


def _hybrid_decision_text(rule_result: str, llm_result: str, policy: str) -> str:
    if policy == "llm_confirm":
        return f"rule={rule_result}; llm={llm_result}; final follows llm confirmation with downgrade on conflict"
    if policy == "require_both":
        return f"rule={rule_result}; llm={llm_result}; matched only when both matched"
    return f"rule={rule_result}; llm={llm_result}; rule_first used llm because rule did not match"


def _evaluate_rule_indicator(  # noqa: PLR0913
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    profile: dict[str, Any],
    indicator_evidence: list[dict[str, Any]] | None = None,
) -> BusinessIndicatorResult:
    indicator_evidence = indicator_evidence or []
    if indicator.data_sources:
        matched = any(bool(item.get("matched")) for item in indicator_evidence)
        data_source_result: BusinessResult = "matched" if matched else "not_matched"
        evidence = [str(item.get("evidence")) for item in indicator_evidence if item.get("evidence")]
        current_status = "；".join(evidence[:2]) if evidence else "未命中已配置的本地数据源。"
        return _indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            result=data_source_result,
            confidence="高" if matched else "中",
            current_status=current_status,
            evidence=evidence,
            evidence_details=indicator_evidence,
        )
    if indicator.rule is None:
        msg = f"missing rule config: {indicator.indicator_id}"
        raise ValueError(msg)
    value = get_nested(profile, indicator.rule.source_field)
    matched = _compare(value, indicator.rule.op, indicator.rule.value)
    rule_result: BusinessResult = "matched" if matched else "not_matched"
    evidence = [f"{indicator.rule.source_field} = {_display_value(value)}"] if value not in (None, "", [], {}) else []
    current_status = _rule_current_status(indicator, value, matched=matched)
    web_evidence = [
        item
        for item in indicator_evidence
        if item.get("source_type") == "web" and item.get("matched") and item.get("evidence")
    ]
    if (
        rule_result != "matched"
        and indicator.web_search is not None
        and (indicator.web_search.effect or "evidence_only") == "possible_on_evidence"
        and web_evidence
    ):
        evidence = [str(item.get("evidence")) for item in web_evidence[:3]]
        return _indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            result="possible",
            confidence="中",
            current_status="；".join(evidence[:2]),
            evidence=evidence,
            evidence_details=indicator_evidence,
            web_search_trace=_render_web_search_trace(
                company_name=str(profile.get("company_name") or ""),
                indicator=indicator,
            ),
        )
    return _indicator_result(
        config=config,
        module=module,
        label=label,
        indicator=indicator,
        result=rule_result,
        confidence="高" if matched else "中",
        current_status=current_status,
        evidence=evidence,
        evidence_details=indicator_evidence,
        web_search_trace=_render_web_search_trace(
            company_name=str(profile.get("company_name") or ""),
            indicator=indicator,
        ),
    )


async def _evaluate_llm_indicator(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    indicator_evidence: list[dict[str, Any]] | None = None,
    llm_debug: bool,
    llm_events: list[dict[str, Any]],
) -> BusinessIndicatorResult:
    key = _indicator_key(module, label, indicator)
    indicator_evidence = indicator_evidence or []
    web_trace = _render_web_search_trace(company_name=company_name, indicator=indicator)
    payload = {
        "company_name": company_name,
        "company_profile": _compact_profile(profile),
        "indicator_evidence": _compact_indicator_evidence(indicator_evidence),
        "web_search": web_trace,
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
    request_summary = {
        "module_id": module.module_id,
        "label_id": label.label_id,
        "indicator_id": indicator.indicator_id,
        "evidence_items": len(payload["indicator_evidence"]),
        "timeout_seconds": LLM_TIMEOUT_SECONDS,
    }
    if llm_debug:
        print_llm_start(title=f"业务指标:{key}", model=settings.llm_model, request=request_summary)
    started = perf_counter()
    try:
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
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, RuntimeError, TypeError, ValueError) as exc:
        llm_events.append(
            llm_event(
                stage="business_indicator",
                name=key,
                model=settings.llm_model,
                status="failed",
                elapsed_seconds=perf_counter() - started,
                request=request_summary,
                system_prompt=system,
                user_payload=payload,
                parameters={"temperature": 0.0, "timeout_seconds": LLM_TIMEOUT_SECONDS},
                error=exc,
            )
        )
        setattr(exc, "_xft_llm_event_recorded", True)  # noqa: B010
        if llm_debug:
            print_llm_failure(
                title=f"业务指标:{key}",
                elapsed_seconds=perf_counter() - started,
                error=exc,
                fallback="本地 evidence_hints 兜底判断",
            )
        raise
    llm_events.append(
        llm_event(
            stage="business_indicator",
            name=key,
            model=settings.llm_model,
            status="success",
            elapsed_seconds=perf_counter() - started,
            request=request_summary,
            response_preview=preview_text(raw),
            response_text=raw,
            system_prompt=system,
            user_payload=payload,
            parameters={"temperature": 0.0, "timeout_seconds": LLM_TIMEOUT_SECONDS},
            result=parsed.result,
            confidence=parsed.confidence,
        )
    )
    if llm_debug:
        print_llm_success(
            title=f"业务指标:{key}",
            elapsed_seconds=perf_counter() - started,
            result=parsed.result,
            confidence=parsed.confidence,
            raw=raw,
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
        evidence_details=indicator_evidence,
        web_search_trace=web_trace,
    )


def _indicator_key(
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
) -> str:
    return f"{module.module_id}.{label.label_id}.{indicator.indicator_id}"


def _indicator_evidence(
    business_evidence: dict[str, list[dict[str, Any]]],
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
) -> list[dict[str, Any]]:
    return business_evidence.get(_indicator_key(module, label, indicator), [])


def _web_trace_by_indicator(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("indicator_key") or "")
        if key:
            grouped.setdefault(key, []).append(row)
    return grouped


def _with_actual_web_trace(
    result: BusinessIndicatorResult,
    grouped: dict[str, list[dict[str, Any]]],
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
) -> BusinessIndicatorResult:
    actual = grouped.get(_indicator_key(module, label, indicator), [])
    if not actual:
        return result
    return result.model_copy(update={"web_search_trace": actual})


def _compact_indicator_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_type": item.get("source_type"),
            "source": item.get("source"),
            "matched": item.get("matched"),
            "evidence": item.get("evidence"),
            "value": item.get("value"),
            "expected": item.get("expected"),
            "sample_count": item.get("sample_count"),
        }
        for item in items[:MAX_EVIDENCE_ITEMS]
    ]


def _render_web_search_trace(company_name: str, indicator: BusinessIndicatorConfig) -> list[dict[str, Any]]:
    if indicator.web_search is None:
        return []
    trace: list[dict[str, Any]] = [
        {
            "query": query.format(company_name=company_name),
            "status": "planned",
            "auto": False,
            "when": indicator.web_search.when,
            "effect": indicator.web_search.effect,
            "note": "indicator-level fixed query",
        }
        for query in indicator.web_search.fixed_queries
    ]
    if indicator.web_search.auto.enabled:
        trace.append(
            {
                "status": "skipped",
                "auto": True,
                "when": indicator.web_search.when,
                "effect": indicator.web_search.effect,
                "max_auto_rounds": indicator.web_search.max_auto_rounds,
                "max_queries": indicator.web_search.auto.max_queries,
                "intent": indicator.web_search.auto.intent,
                "note": "auto query generation is configured but not executed in planned trace",
            }
        )
    return trace


def _fallback_indicator_result(  # noqa: PLR0913
    *,
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
    profile: dict[str, Any],
    indicator_evidence: list[dict[str, Any]] | None = None,
) -> BusinessIndicatorResult:
    indicator_evidence = indicator_evidence or []
    source_evidence = [
        str(item.get("evidence")) for item in indicator_evidence if item.get("matched") and item.get("evidence")
    ]
    evidence = source_evidence or _hint_evidence(profile, indicator.evidence_hints)
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
        evidence_details=indicator_evidence,
        web_search_trace=_render_web_search_trace(
            company_name=str(profile.get("company_name") or ""),
            indicator=indicator,
        ),
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
    evidence_details: list[dict[str, Any]] | None = None,
    web_search_trace: list[dict[str, Any]] | None = None,
    hybrid_trace: dict[str, Any] | None = None,
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
        evidence_details=(evidence_details or [])[:24],
        web_search_trace=(web_search_trace or [])[:24],
        evaluator=indicator.evaluator,
        hybrid_trace=hybrid_trace or {},
    )


def _acceptance(
    config: BusinessRecommendationConfig,
    module: BusinessModuleConfig,
    attributes_number: int,
    indicators_number: int,
) -> tuple[str, str]:
    policy = module.acceptance_policy or config.acceptance_policy
    levels = sorted(policy.levels, key=lambda item: item.min_matched_labels, reverse=True)
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


def _compare(value: Any, op: str, expected: Any) -> bool:  # noqa: PLR0911
    if op == "exists":
        return value not in (None, "", [], {})
    if op == "contains":
        return contains(value, expected)
    if op == "contains_any":
        values = expected if isinstance(expected, list) else [expected]
        return any(contains(value, item) for item in values)
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
