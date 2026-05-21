"""Human-readable recommender run log rendering."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xft.pipeline.recommender.evidence_utils import merge_indicator_evidence
from xft.pipeline.recommender.models import IndicatorConfig, LabelConfig, ModuleConfig
from xft.pipeline.recommender.state import RecommenderState
from xft.utils.misc import get_nested

MAX_TEXT = 220
MAX_EVIDENCE = 8
MAX_WEB_RESULTS = 5
MAX_TUNING_FINDINGS = 30


def write_run_log(
    *,
    out_dir: Path,
    state: RecommenderState,
    llm_metrics: dict[str, Any],
) -> Path:
    """Write the human-readable run log and return its path."""
    path = _log_path(out_dir, state["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_run_log(state=state, llm_metrics=llm_metrics), encoding="utf-8")
    return path


def write_failure_log(
    *,
    out_dir: Path,
    company_name: str,
    run_id: str,
    error: str,
    context: dict[str, Any] | None = None,
) -> Path:
    """Write a small human-readable log for failures before save_node runs."""
    path = _log_path(out_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 推荐运行日志：{company_name}",
        "",
        f"- run_id: {run_id}",
        f"- 生成时间: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "- 状态: failed",
        f"- 错误: {error}",
    ]
    if context:
        lines.extend(["", "## 运行上下文"])
        for key, value in context.items():
            lines.append(f"- {key}: {_short(value)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_run_log(*, state: RecommenderState, llm_metrics: dict[str, Any]) -> str:
    """Render a full, human-readable explanation of one recommendation run."""
    business = state.get("recommendation")
    config = state.get("modules_config")
    profile = state.get("profile", {})
    llm_events = state.get("llm_call_events", [])
    evidence = merge_indicator_evidence(state.get("evidence", {}), state.get("web_evidence", {}))
    web_trace_by_indicator = _group_web_trace(state.get("web_trace", []))
    llm_by_indicator = {str(event.get("name") or ""): event for event in llm_events}

    lines: list[str] = []
    lines.extend(_header(state, llm_metrics))
    lines.extend(_profile_section(profile))
    lines.extend(_config_section(config))
    lines.extend(_evidence_overview(evidence))

    if business is None:
        lines.extend(["", "## 推荐结果", "未生成推荐结果。"])
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(_result_summary(business))
    lines.extend(_tuning_summary(state=state, evidence=evidence, llm_events=llm_events))
    config_by_key = _config_by_indicator(config)
    for module_result in business.modules:
        lines.extend(
            [
                "",
                f"## 模块：{module_result.module_name} ({module_result.module_id})",
                f"- 模块分: {module_result.score}",
                f"- 接受度: {module_result.acceptance_result}",
                f"- 命中标签/指标: {module_result.attributes_number}/{module_result.indicators_number}",
                f"- 结论: {module_result.conclusion}",
            ]
        )
        for label_result in module_result.label_results:
            lines.extend(
                [
                    "",
                    f"### 标签：{label_result.label_name} ({label_result.label_id})",
                    f"- 判断: {label_result.result}",
                    f"- 命中指标: {label_result.matched_indicators}",
                    f"- 可能指标: {label_result.possible_indicators}",
                    f"- 标签分: {label_result.score}",
                ]
            )
            for indicator_result in label_result.indicator_results:
                key = f"{indicator_result.module_id}.{indicator_result.label_id}.{indicator_result.indicator_id}"
                cfg = config_by_key.get(key)
                lines.extend(
                    _indicator_section(
                        indicator=indicator_result,
                        cfg=cfg,
                        profile=profile,
                        evidence=evidence.get(key, []),
                        web_trace=web_trace_by_indicator.get(key, indicator_result.web_search_trace),
                        llm_event=llm_by_indicator.get(key),
                        with_web=bool(state.get("with_web")),
                    )
                )
    if business.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in business.warnings)
    return "\n".join(lines).rstrip() + "\n"


def _log_path(out_dir: Path, run_id: str) -> Path:
    return out_dir / "logs" / f"{run_id}.log"


def _header(state: RecommenderState, llm_metrics: dict[str, Any]) -> list[str]:
    return [
        f"# 推荐运行日志：{state['company_name']}",
        "",
        f"- run_id: {state['run_id']}",
        f"- 生成时间: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- 场景: {state.get('scenario_name') or ''} ({state.get('scenario_id') or ''})",
        f"- DuckDB: {state.get('warehouse_db')}",
        f"- LLM: {'启用' if state.get('use_llm') else '关闭'}",
        f"- LLM 并发: {state.get('llm_concurrency')}",
        f"- Web: {'启用' if state.get('with_web') else '关闭'}",
        f"- Web refresh: {state.get('refresh_web')}",
        f"- Web providers: {_short(state.get('web_providers') or [])}",
        (
            f"- LLM 调用: {llm_metrics.get('total', 0)} 次，"
            f"成功 {llm_metrics.get('success', 0)}，失败 {llm_metrics.get('failed', 0)}"
        ),
    ]


def _profile_section(profile: dict[str, Any]) -> list[str]:
    raw_basic = profile.get("basic")
    basic: dict[str, Any] = raw_basic if isinstance(raw_basic, dict) else {}
    rows = [
        "",
        "## 企业画像摘要",
        f"- 企业名称: {profile.get('company_name') or basic.get('company_name') or ''}",
        f"- 统一社会信用代码: {profile.get('credit_code') or ''}",
        f"- 行业: {profile.get('industry') or basic.get('industry') or ''}",
        f"- 行业中类: {profile.get('industry_mid') or ''}",
        f"- 行业小类: {profile.get('industry_small') or ''}",
        f"- 画像完整度: {profile.get('profile_completeness', '')}",
        f"- 标签: {_short(profile.get('labels') or [])}",
        f"- 经营范围: {_short(profile.get('business_scope') or basic.get('business_scope') or '')}",
    ]
    return rows


def _config_section(config: Any) -> list[str]:
    if config is None:
        return ["", "## 推荐配置", "未加载 modules.yaml。"]
    module_ids = [module.module_id for module in config.modules]
    label_ids = [label.label_id for module in config.modules for label in module.labels]
    indicator_count = sum(len(label.indicators) for module in config.modules for label in module.labels)
    evaluator_counts: dict[str, int] = {}
    for module in config.modules:
        for label in module.labels:
            for indicator in label.indicators:
                evaluator_counts[indicator.evaluator] = evaluator_counts.get(indicator.evaluator, 0) + 1
    return [
        "",
        "## 推荐配置",
        f"- 参与模块: {'、'.join(module_ids)}",
        f"- 模块数: {len(config.modules)}",
        f"- 参与标签: {'、'.join(label_ids)}",
        f"- 标签数: {len(label_ids)}",
        f"- 指标数: {indicator_count}",
        f"- evaluator 分布: {_short(evaluator_counts)}",
    ]


def _evidence_overview(evidence: dict[str, list[dict[str, Any]]]) -> list[str]:
    local_count = sum(1 for rows in evidence.values() for row in rows if row.get("source_type") != "web")
    web_count = sum(1 for rows in evidence.values() for row in rows if row.get("source_type") == "web")
    matched_count = sum(1 for rows in evidence.values() for row in rows if row.get("matched"))
    return [
        "",
        "## 证据总览",
        f"- 有证据的指标: {len(evidence)}",
        f"- 本地证据: {local_count}",
        f"- Web 证据: {web_count}",
        f"- 命中证据: {matched_count}",
    ]


def _result_summary(business: Any) -> list[str]:
    selected = business.selected_module
    lines = ["", "## 最终推荐"]
    if selected is None:
        lines.append("未选出推荐模块。")
        return lines
    lines.extend(
        [
            f"- 推荐模块: {selected.module_name} ({selected.module_id})",
            f"- 接受度: {selected.acceptance_result}",
            f"- 模块分: {selected.score}",
            f"- 命中标签/指标: {selected.attributes_number}/{selected.indicators_number}",
            f"- 结论: {selected.conclusion}",
        ]
    )
    return lines


def _tuning_summary(  # noqa: C901
    *,
    state: RecommenderState,
    evidence: dict[str, list[dict[str, Any]]],
    llm_events: list[dict[str, Any]],
) -> list[str]:
    business = state.get("recommendation")
    if business is None:
        return []
    lines = ["", "## 调优建议摘要"]
    findings: list[str] = []
    for indicator in business.indicator_results:
        key = f"{indicator.module_id}.{indicator.label_id}.{indicator.indicator_id}"
        if indicator.result == "unknown":
            findings.append(f"- unknown 指标: {key}，优先补证据、调 prompt 或调整 evaluator。")
        if not evidence.get(key):
            findings.append(f"- 无证据指标: {key}，检查 data_sources / web_search 是否覆盖。")
        web_rows = indicator.web_search_trace
        for row in web_rows:
            if row.get("status") == "skipped":
                findings.append(
                    f"- Web 未搜索: {key}，原因 {row.get('trigger_reason') or row.get('reason') or 'unknown'}。"
                )
            elif int(row.get("result_count") or 0) == 0 and row.get("query"):
                findings.append(f"- Web 零结果: {key}，查询词 `{_short(row.get('query'), limit=80)}`。")
    findings.extend(
        f"- LLM 失败: {event.get('name')}，错误 {_short(event.get('error'))}。"
        for event in llm_events
        if event.get("status") == "failed"
    )
    if not findings:
        findings.append("- 未发现明显调优告警；可继续抽查命中证据和搜索结果质量。")
    lines.extend(findings[:MAX_TUNING_FINDINGS])
    if len(findings) > MAX_TUNING_FINDINGS:
        lines.append(f"- ... 还有 {len(findings) - MAX_TUNING_FINDINGS} 条调优提示")
    return lines


def _indicator_section(  # noqa: PLR0913
    *,
    indicator: Any,
    cfg: tuple[ModuleConfig, LabelConfig, IndicatorConfig] | None,
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
    web_trace: list[dict[str, Any]],
    llm_event: dict[str, Any] | None,
    with_web: bool,
) -> list[str]:
    configured = cfg[2] if cfg else None
    lines = [
        "",
        f"#### 指标：{indicator.indicator_name} ({indicator.indicator_id})",
        f"- evaluator: {indicator.evaluator}",
        f"- 判断结果: {indicator.result} / 置信度: {indicator.confidence} / 分数: {indicator.score}",
        f"- 判断标准: {indicator.standard}",
        f"- 当前状态: {indicator.current_status}",
    ]
    if configured and configured.rule:
        value = get_nested(profile, configured.rule.source_field)
        lines.extend(
            [
                "- Rule 决策点:",
                f"  - 字段: {configured.rule.source_field}",
                f"  - 操作: {configured.rule.op}",
                f"  - 期望: {_short(configured.rule.value)}",
                f"  - 实际: {_short(value)}",
            ]
        )
    if configured and configured.data_sources:
        lines.append("- Data sources 决策点:")
        for source in configured.data_sources:
            target = source.path if source.type == "field" else f"{source.table}.{source.field}"
            expected = _short(source.value or source.keywords)
            lines.append(f"  - {source.type}: {target}; op={source.op}; expected={expected}; limit={source.limit}")
    lines.extend(_evidence_lines(evidence))
    if configured and configured.web_search:
        lines.extend(
            [
                "- Web policy:",
                f"  - when: {configured.web_search.when or '默认'}",
                f"  - effect: {configured.web_search.effect or '默认'}",
                f"  - fixed_queries: {_short(configured.web_search.fixed_queries)}",
                (
                    f"  - auto: enabled={configured.web_search.auto.enabled}, "
                    f"max_queries={configured.web_search.auto.max_queries}"
                ),
            ]
        )
    lines.extend(_web_lines(web_trace, configured=configured, with_web=with_web))
    lines.extend(_llm_lines(llm_event))
    if indicator.hybrid_trace:
        lines.append("- Hybrid trace:")
        for key, value in indicator.hybrid_trace.items():
            lines.append(f"  - {key}: {_short(value)}")
    if indicator.evidence:
        lines.append("- 最终采纳证据:")
        lines.extend(f"  - {_short(item)}" for item in indicator.evidence[:MAX_EVIDENCE])
    return lines


def _evidence_lines(evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return ["- 证据: 无"]
    lines = [f"- 证据: {len(evidence)} 条"]
    for item in evidence[:MAX_EVIDENCE]:
        source = item.get("source") or item.get("source_type") or ""
        matched = "命中" if item.get("matched") else "未命中"
        detail = item.get("evidence") or item.get("value") or item.get("error") or ""
        extra = ""
        if item.get("sample_count") is not None:
            extra = f"; sample_count={item.get('sample_count')}"
        lines.append(f"  - [{matched}] {source}: {_short(detail)}{extra}")
    if len(evidence) > MAX_EVIDENCE:
        lines.append(f"  - ... 还有 {len(evidence) - MAX_EVIDENCE} 条")
    return lines


def _web_lines(
    web_trace: list[dict[str, Any]],
    *,
    configured: IndicatorConfig | None,
    with_web: bool,
) -> list[str]:
    if configured and configured.web_search and not with_web:
        return ["- Web 执行: skipped; reason=web_disabled"]
    if not web_trace:
        if configured and configured.web_search:
            return ["- Web 执行: skipped; reason=policy_not_triggered_or_no_query"]
        return ["- Web 执行: 无"]
    lines = [f"- Web 执行: {len(web_trace)} 条 trace"]
    for row in web_trace:
        query = row.get("query") or ""
        status = row.get("status") or ""
        reason = row.get("trigger_reason") or row.get("reason") or ""
        result_count = row.get("result_count")
        filtered_count = row.get("filtered_result_count")
        fetch_filtered = row.get("fetch_relevance_filtered_count")
        query_text = _short(query)
        lines.append(
            f"  - query={query_text}; status={status}; reason={reason}; "
            f"result_count={result_count}; filtered={filtered_count}; fetch_filtered={fetch_filtered}"
        )
        lines.extend(
            f"    - fetch filtered: {item.get('reason')} | {_short(item.get('title'))} | {_short(item.get('url'))}"
            for item in _list(row.get("fetch_relevance_filtered"))
            if isinstance(item, dict)
        )
        lines.extend(
            f"    - {_short(result.get('title'))} | {_short(result.get('url'))} | {_short(result.get('snippet'))}"
            for result in _list(row.get("results"))[:MAX_WEB_RESULTS]
            if isinstance(result, dict)
        )
    return lines


def _llm_lines(event: dict[str, Any] | None) -> list[str]:
    if not event:
        return ["- LLM 执行: 未调用"]
    lines = [
        "- LLM 执行:",
        f"  - status: {event.get('status')}",
        f"  - model: {event.get('model')}",
        f"  - elapsed_seconds: {event.get('elapsed_seconds')}",
        f"  - result: {event.get('result')}",
        f"  - confidence: {event.get('confidence')}",
    ]
    request = event.get("request")
    if isinstance(request, dict):
        lines.append(f"  - evidence_items: {request.get('evidence_items')}")
    if event.get("response_preview"):
        lines.append(f"  - response_preview: {_short(event.get('response_preview'))}")
    if event.get("error"):
        lines.append(f"  - error: {_short(event.get('error'))}")
    return lines


def _config_by_indicator(config: Any) -> dict[str, tuple[ModuleConfig, LabelConfig, IndicatorConfig]]:
    if config is None:
        return {}
    return {
        f"{module.module_id}.{label.label_id}.{indicator.indicator_id}": (module, label, indicator)
        for module in config.modules
        for label in module.labels
        for indicator in label.indicators
    }


def _group_web_trace(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("indicator_key") or "")
        if key:
            grouped.setdefault(key, []).append(row)
    return grouped


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short(value: Any, *, limit: int = MAX_TEXT) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit]}..."
