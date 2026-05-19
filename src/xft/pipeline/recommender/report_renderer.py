"""Markdown report rendering for recommender outputs."""

from __future__ import annotations

from xft.pipeline.recommender.business_models import BusinessIndicatorResult, BusinessModuleResult
from xft.pipeline.recommender.state import RecommenderState


def render_report(state: RecommenderState) -> str:
    """Render a concise Markdown recommendation report."""
    return "\n".join(
        [
            *_render_profile_summary(state),
            *_render_evidence_summary(state),
            *_render_dimension_summary(state),
            *_render_business_result(state),
            *_render_business_modules(state),
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


def _render_evidence_summary(state: RecommenderState) -> list[str]:
    """Render evidence quality, conflicts, and gaps summary."""
    analyses = state["dimension_analysis"]
    lines = ["## 证据摘要", ""]

    total_primary = sum(len(a.local_evidence) for a in analyses)
    total_web = sum(len(a.web_evidence) for a in analyses)
    total_conflicts = sum(len(a.conflicts) for a in analyses)
    total_inferences = sum(len(a.inference_evidence) for a in analyses)
    total_missing = sum(len(a.missing_evidence) for a in analyses)

    lines.append(f"- 本地事实证据：{total_primary} 条")
    lines.append(f"- Web 补证/佐证：{total_web} 条")
    lines.append(f"- 规则推断：{total_inferences} 条")
    lines.append(f"- 数据冲突：{total_conflicts} 处")
    lines.append(f"- 待补充证据：{total_missing} 项")
    lines.append("")

    # Conflicts detail
    conflict_lines = [
        f"  - {analysis.title}：{conflict.claim}" for analysis in analyses for conflict in analysis.conflicts[:2]
    ]
    if conflict_lines:
        lines.append("### 冲突提示")
        lines.append("Web 信息与本地画像存在以下冲突，推荐结论已优先采用本地证据：")
        lines.extend(conflict_lines)
        lines.append("")

    # Missing evidence detail
    missing_dims = [(a.title, a.missing_evidence[:3]) for a in analyses if a.missing_evidence]
    if missing_dims:
        lines.append("### 数据缺口")
        lines.append("以下证据项仍缺失，建议通过访谈或专项调研补充：")
        lines.extend(f"  - {title}：{gap}" for title, gaps in missing_dims[:5] for gap in gaps)
        lines.append("")

    return lines


def _render_dimension_summary(state: RecommenderState) -> list[str]:
    lines = ["## 维度分析摘要", ""]
    for analysis in state["dimension_analysis"]:
        facts = "；".join(fact.claim for fact in analysis.facts[:3]) or "本地数据不足"
        tags: list[str] = []
        if analysis.local_evidence:
            tags.append(f"本地{len(analysis.local_evidence)}条")
        if analysis.web_evidence:
            tags.append(f"Web{len(analysis.web_evidence)}条")
        if analysis.conflicts:
            tags.append(f"冲突{len(analysis.conflicts)}处")
        tag_str = f"（{'，'.join(tags)}）" if tags else ""
        lines.append(f"- {analysis.title}：{analysis.status}，{analysis.confidence}。{facts}{tag_str}")
    lines.append("")
    return lines


def _render_business_result(state: RecommenderState) -> list[str]:
    business = state.get("business_recommendation")
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
            indicators = [
                item.indicator_name
                for item in label.indicator_results
                if item.result == "matched"
            ]
            lines.append(f"- {label.label_name}：{label.key_indicator_verify}（{'、'.join(indicators)}）")
        lines.append("")
    return lines


def _render_business_modules(state: RecommenderState) -> list[str]:
    business = state.get("business_recommendation")
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


def _render_business_module_detail(rank: int, module: BusinessModuleResult) -> list[str]:
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


def _indicator_text(indicator: BusinessIndicatorResult) -> str:
    evidence = "；".join(indicator.evidence[:2]) or indicator.current_status
    return (
        f"{indicator.indicator_name}：{_result_text(indicator.result)}，"
        f"置信度{indicator.confidence}，{indicator.evaluator}，{evidence}"
    )


def _result_text(result: str) -> str:
    return {
        "matched": "满足",
        "possible": "可能满足",
        "not_matched": "不满足",
        "unknown": "证据不足",
    }.get(result, result)


def _render_next_steps(state: RecommenderState) -> list[str]:
    business = state.get("business_recommendation")
    lines = ["## 下一步核实清单", ""]
    gaps = sorted(
        {
            gap
            for analysis in state["dimension_analysis"]
            for gap in analysis.missing_evidence
        }
    )
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
