"""Markdown report rendering for recommender outputs."""

from __future__ import annotations

from xft.pipeline.recommender.models import IndicatorResult, ModuleResult
from xft.pipeline.recommender.state import RecommenderState
from xft.utils.misc import result_text


def render_report(state: RecommenderState) -> str:
    """Render a concise Markdown recommendation report."""
    return "\n".join(
        [
            *_render_profile_summary(state),
            *_render_business_result(state),
            *_render_modules(state),
            *_render_indicator_evidence_summary(state),
            *_render_next_steps(state),
        ]
    )


def _render_profile_summary(state: RecommenderState) -> list[str]:
    profile = state.get("profile", {})
    return [
        "# 产品模块推荐报告",
        "",
        "## 企业画像摘要",
        "",
        f"- 企业名称：{profile.get('company_name', state['company_name'])}",
        f"- 行业：{profile.get('industry') or '未找到'} / {profile.get('industry_big') or '未找到'}",
        f"- 员工社保人数：{profile.get('employee_count') or '未找到'}",
        f"- 画像完整度：{profile.get('profile_completeness', 0)}",
        f"- 需要 Web 补充：{'是' if state['needs_web_enrichment'] else '否'}",
        "",
    ]


def _render_indicator_evidence_summary(state: RecommenderState) -> list[str]:
    business = state.get("recommendation")
    if business is None:
        return []
    indicators = [
        item
        for module in business.modules
        for label in module.label_results
        for item in label.indicator_results
        if item.result in ("matched", "possible") and (item.evidence or item.evidence_details or item.web_search_trace)
    ]
    if not indicators:
        return []
    lines = ["## 指标证据", ""]
    for indicator in indicators[:20]:
        lines.append(f"- {indicator.module_name} / {indicator.label_name} / {indicator.indicator_name}")
        local = [
            item for item in indicator.evidence_details if item.get("source_type") != "web" and item.get("evidence")
        ]
        web = [item for item in indicator.evidence_details if item.get("source_type") == "web"]
        if local:
            lines.append("  - 本地证据：" + "；".join(str(item.get("evidence")) for item in local[:2]))
        if web:
            lines.append("  - Web 证据：" + "；".join(str(item.get("evidence")) for item in web[:2]))
            urls = [str(item.get("url")) for item in web if item.get("url")]
            if urls:
                lines.append("  - Web 来源：" + "；".join(urls[:2]))
        if not local and not web:
            lines.append(f"  - 当前判断：{indicator.current_status}")
        web_queries = [str(item.get("query")) for item in indicator.web_search_trace if item.get("query")]
        if web_queries:
            lines.append(f"  - Web 查询：{'；'.join(web_queries[:2])}")
    lines.append("")
    return lines


def _render_business_result(state: RecommenderState) -> list[str]:
    business = state.get("recommendation")
    if business is None or business.selected_module is None:
        return []
    selected = business.selected_module
    lines = [
        "## 业务推荐结果",
        "",
        f"- 推荐模块：{selected.module_name}",
        f"- 接受度：{selected.acceptance_result}",
        f"- 命中属性：{selected.attributes_number} 个",
        f"- 命中指标：{selected.indicators_number} 个",
        f"- 结论：{selected.conclusion}",
        "",
    ]
    matched_labels = [item for item in selected.label_results if item.result == "matched"]
    if matched_labels:
        lines.append("### 命中标签")
        for label in matched_labels:
            indicators = [item.indicator_name for item in label.indicator_results if item.result == "matched"]
            lines.append(f"- {label.label_name}：{label.key_indicator_verify}（{'、'.join(indicators)}）")
        lines.append("")
    return lines


def _render_modules(state: RecommenderState) -> list[str]:
    business = state.get("recommendation")
    lines = ["## 推荐模块总览", ""]
    if business is None:
        lines.append("业务推荐结果生成失败。")
        lines.append("")
        return lines
    modules = sorted(business.modules, key=lambda item: (-item.score, -item.attributes_number, item.module_id))
    lines.append("| 模块 | 接受度 | 分数 | 命中属性 | 命中指标 |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    lines.extend(
        (
            f"| {module.module_name} | {module.acceptance_result} | {module.score} | "
            f"{module.attributes_number} | {module.indicators_number} |"
        )
        for module in modules
    )
    lines.append("")
    for idx, module in enumerate(modules, 1):
        lines.extend(_render_business_module_detail(idx, module))
    return lines


def _render_business_module_detail(rank: int, module: ModuleResult) -> list[str]:
    lines = [
        f"### {rank}. {module.module_name}",
        "",
        f"- 接受度：{module.acceptance_result}",
        f"- 业务分：{module.score}",
        f"- 命中属性：{module.attributes_number} 个",
        f"- 命中指标：{module.indicators_number} 个",
        f"- 结论：{module.conclusion}",
    ]
    matched_labels = [item for item in module.label_results if item.result == "matched"]
    possible_labels = [item for item in module.label_results if item.result == "possible"]
    if matched_labels:
        lines.append("- 命中标签：" + "；".join(item.label_name for item in matched_labels))
    if possible_labels:
        lines.append("- 可能标签：" + "；".join(item.label_name for item in possible_labels))
    for label in [*matched_labels, *possible_labels][:4]:
        indicators = [item for item in label.indicator_results if item.result in ("matched", "possible")]
        if not indicators:
            continue
        lines.append(f"  - {label.label_name}：{label.key_indicator_verify}")
        lines.extend("    - " + _indicator_text(indicator) for indicator in indicators[:4])
    lines.append("")
    return lines


def _indicator_text(indicator: IndicatorResult) -> str:
    evidence = "；".join(indicator.evidence[:2]) or indicator.current_status
    return (
        f"{indicator.indicator_name}：{result_text(indicator.result)}，"
        f"置信度{indicator.confidence}，{indicator.evaluator}，{evidence}"
    )


def _render_next_steps(state: RecommenderState) -> list[str]:
    business = state.get("recommendation")
    lines = ["## 下一步核实清单", ""]
    gaps: list[str] = []
    if business:
        gaps = sorted(
            {
                str(detail.get("source"))
                for indicator in business.indicator_results
                for detail in indicator.evidence_details
                if not detail.get("matched") and detail.get("source")
            }
        )
    if business:
        no_result_queries = [
            str(trace.get("query"))
            for indicator in business.indicator_results
            for trace in indicator.web_search_trace
            if trace.get("query") and trace.get("result_count") == 0
        ]
        unknown_indicators = [
            f"{indicator.label_name}/{indicator.indicator_name}"
            for indicator in business.indicator_results
            if indicator.result == "unknown"
        ]
        gaps = [*unknown_indicators[:3], *gaps, *no_result_queries[:3]]
    if business and business.selected_module:
        selected = business.selected_module
        lines.append(f"1. 围绕「{selected.module_name}」确认预算、现有系统、审批链路和落地时间。")
    if gaps:
        start = 2 if business and business.selected_module else 1
        for idx, gap in enumerate(gaps[:6], start):
            lines.append(f"{idx}. {gap}")
    elif not (business and business.selected_module):
        lines.append("1. 暂无明确数据缺口，建议先补充企业基础画像后再进行推荐。")
    lines.append("")
    return lines
