"""Scenario configuration audit for recommender tuning."""

from __future__ import annotations

from typing import Any

from xft.pipeline.recommender.models import IndicatorConfig, RecommendationConfig

GENERIC_QUERY_TERMS = {"官网", "新闻", "公司", "企业", "信息", "介绍", "招聘", "公开"}


def audit_recommendation_config(config: RecommendationConfig | None) -> dict[str, Any]:
    """Return a compact audit report for recommendation module configuration."""
    if config is None:
        return {"modules": 0, "indicators": 0, "evaluator_counts": {}, "warnings": ["modules config not loaded"]}
    evaluator_counts: dict[str, int] = {}
    modules: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    indicator_count = 0
    for module in config.modules:
        module_counts: dict[str, int] = {}
        module_indicator_count = 0
        for label in module.labels:
            for indicator in label.indicators:
                indicator_count += 1
                module_indicator_count += 1
                evaluator_counts[indicator.evaluator] = evaluator_counts.get(indicator.evaluator, 0) + 1
                module_counts[indicator.evaluator] = module_counts.get(indicator.evaluator, 0) + 1
                warnings.extend(_indicator_warnings(module.module_id, label.label_id, indicator))
        modules.append(
            {
                "module_id": module.module_id,
                "module_name": module.module_name,
                "labels": len(module.labels),
                "indicators": module_indicator_count,
                "evaluator_counts": module_counts,
            }
        )
    return {
        "modules": len(config.modules),
        "indicators": indicator_count,
        "evaluator_counts": evaluator_counts,
        "module_summaries": modules,
        "warnings": warnings,
    }


def render_audit_text(payload: dict[str, Any]) -> str:
    """Render an audit report for humans."""
    lines = [
        "# 推荐场景配置审计",
        "",
        f"- 模块数: {payload.get('modules', 0)}",
        f"- 指标数: {payload.get('indicators', 0)}",
        f"- evaluator 分布: {payload.get('evaluator_counts', {})}",
        "",
        "## 模块概览",
    ]
    lines.extend(
        (
            f"- {module.get('module_name')} ({module.get('module_id')}): "
            f"{module.get('indicators')} 个指标，{module.get('evaluator_counts')}"
        )
        for module in payload.get("module_summaries", [])
    )
    lines.extend(["", "## 配置告警"])
    warnings = payload.get("warnings", [])
    if not warnings:
        lines.append("- 未发现明显配置告警。")
    else:
        lines.extend(
            (
                f"- [{warning.get('severity')}] {warning.get('indicator_key')}: "
                f"{warning.get('message')} 建议：{warning.get('suggestion')}"
            )
            for warning in warnings
        )
    return "\n".join(lines).rstrip() + "\n"


def _indicator_warnings(module_id: str, label_id: str, indicator: IndicatorConfig) -> list[dict[str, str]]:
    key = f"{module_id}.{label_id}.{indicator.indicator_id}"
    warnings: list[dict[str, str]] = []
    if indicator.evaluator == "llm_web" and indicator.web_search is None:
        warnings.append(_warning(key, "high", "llm_web 缺少 web_search。", "补充固定查询词或改为 llm/hybrid。"))
    if indicator.evaluator == "llm_web" and not indicator.data_sources:
        warnings.append(_warning(key, "low", "llm_web 完全依赖 Web。", "确认 DuckDB 无法覆盖后再保留 llm_web。"))
    if indicator.evaluator in ("llm", "hybrid") and not indicator.data_sources and indicator.web_search is None:
        warnings.append(_warning(key, "medium", "LLM 指标缺少本地证据和 Web 补证。", "补 data_sources 或 web_search。"))
    if indicator.evaluator == "rule" and indicator.web_search and indicator.web_search.effect == "possible_on_evidence":
        warnings.append(_warning(key, "info", "rule 使用 Web possible_on_evidence。", "确认 Web 只能补到 possible。"))
    warnings.extend(
        _warning(key, "high", "text_contains 缺少 keywords。", "配置明确关键词或改用 exists。")
        for source in indicator.data_sources
        if source.op == "text_contains" and not source.keywords and source.value in (None, "")
    )
    if indicator.web_search:
        warnings.extend(
            _warning(key, "medium", f"查询词过泛: {query}", "加入指标词、业务词或场景词。")
            for query in indicator.web_search.fixed_queries
            if _is_generic_query(query)
        )
    return warnings


def _warning(indicator_key: str, severity: str, message: str, suggestion: str) -> dict[str, str]:
    return {
        "indicator_key": indicator_key,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
    }


def _is_generic_query(query: str) -> bool:
    normalized = query.replace("{company_name}", " ").strip()
    tokens = [token for token in normalized.split() if token]
    return not tokens or all(token in GENERIC_QUERY_TERMS for token in tokens)
