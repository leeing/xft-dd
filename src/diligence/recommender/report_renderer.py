"""Markdown report rendering for recommender outputs."""

from __future__ import annotations

from diligence.recommender.models import RecommendationItem, ScoreRuleTrace
from diligence.recommender.state import RecommenderState

HIGH_SCORE_THRESHOLD = 75
MEDIUM_SCORE_THRESHOLD = 55
LOW_SCORE_THRESHOLD = 35


def render_report(state: RecommenderState) -> str:
    """Render a concise Markdown recommendation report."""
    return "\n".join(
        [
            *_render_profile_summary(state),
            *_render_evidence_summary(state),
            *_render_dimension_summary(state),
            *_render_recommendations(state),
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
        f"- 员工规模：{profile.get('employee_count') or '未找到'}",
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
        f"  - {analysis.title}：{conflict.claim}"
        for analysis in analyses
        for conflict in analysis.conflicts[:2]
    ]
    if conflict_lines:
        lines.append("### 冲突提示")
        lines.append("Web 信息与本地画像存在以下冲突，推荐结论已优先采用本地证据：")
        lines.extend(conflict_lines)
        lines.append("")

    # Missing evidence detail
    missing_dims = [
        (a.title, a.missing_evidence[:3])
        for a in analyses
        if a.missing_evidence
    ]
    if missing_dims:
        lines.append("### 数据缺口")
        lines.append("以下证据项仍缺失，建议通过访谈或专项调研补充：")
        lines.extend(
            f"  - {title}：{gap}" for title, gaps in missing_dims[:5] for gap in gaps
        )
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
        lines.append(
            f"- {analysis.title}：{analysis.status}，{analysis.confidence}。{facts}{tag_str}"
        )
    lines.append("")
    return lines


def _render_recommendations(state: RecommenderState) -> list[str]:
    rec = state["recommendation"]
    lines = ["## 推荐模块", ""]
    if rec is None:
        lines.append("推荐结果生成失败。")
        return lines
    lines.extend([rec.summary, ""])
    lines.extend(_render_scoring_overview(state))

    for rec_item in rec.recommendations:
        lines.extend(_render_recommendation_detail(rec_item))

    # Next steps section
    lines.append("## 下一步核实清单")
    lines.append("")
    all_gaps: list[str] = []
    for rec_item in rec.recommendations[:3]:
        all_gaps.extend(rec_item.data_gaps[:2])
    all_gaps = sorted(set(all_gaps))[:8]
    if all_gaps:
        for idx, gap in enumerate(all_gaps, 1):
            lines.append(f"{idx}. {gap}")
    else:
        lines.append("暂无明确数据缺口，建议围绕推荐模块进行深度需求访谈。")
    lines.append("")

    return lines


def _render_recommendation_detail(rec_item: RecommendationItem) -> list[str]:
    related_dims = rec_item.evidence_dimensions[:4]
    data_gaps = rec_item.data_gaps[:4]
    breakdown = rec_item.score_breakdown
    lines = [
        f"### {rec_item.rank}. {rec_item.module_name}",
        "",
        f"- 推荐分：{rec_item.score}",
        f"- 分数构成：{_score_parts(rec_item)}",
        f"- 业务需求：{rec_item.business_need}",
        f"- 推荐理由：{rec_item.reason}",
        f"- 建议话术：{rec_item.suggested_pitch}",
        f"- 证据维度：{'；'.join(related_dims) or '本地证据不足'}",
    ]
    if breakdown.matched_rules:
        lines.append("- 命中规则：" + _rule_text(breakdown.matched_rules[:4]))
    if breakdown.penalty_rules:
        lines.append("- 扣分规则：" + _rule_text(breakdown.penalty_rules[:4]))
    if breakdown.exclusion_rules:
        lines.append("- 排除/限制：" + _rule_text(breakdown.exclusion_rules[:2]))
    if rec_item.evidence_trace:
        lines.append("- 核心证据：" + "；".join(item.claim for item in rec_item.evidence_trace[:3]))
    if data_gaps:
        lines.append(f"- 待核实：{'；'.join(data_gaps)}")
    lines.append("")
    return lines


def _score_parts(rec_item: RecommendationItem) -> str:
    breakdown = rec_item.score_breakdown
    parts = [
        f"基础 {breakdown.base_priority}",
        f"维度 {breakdown.dimension_support}",
        f"证据 {breakdown.evidence_support}",
        f"Web {breakdown.web_support}",
        f"规则加分 {breakdown.positive_score}",
        f"规则扣分 {breakdown.negative_score}",
        f"缺口扣分 {breakdown.missing_evidence_penalty}",
        f"冲突扣分 {breakdown.conflict_penalty}",
    ]
    return "，".join(parts)


def _rule_text(rules: list[ScoreRuleTrace]) -> str:
    return "；".join(f"{rule.rule_id}：{rule.reason}" for rule in rules)


def _render_scoring_overview(state: RecommenderState) -> list[str]:
    rec = state["recommendation"]
    if rec is None:
        return []
    lines = ["### 推荐评分总览", ""]
    lines.append("| 产品 | 分数 | 等级 | 规则加分 | 规则扣分 | 主要风险 |")
    lines.append("| --- | ---: | --- | ---: | ---: | --- |")
    for item in rec.recommendations:
        breakdown = item.score_breakdown
        risk_parts: list[str] = []
        if breakdown.conflict_penalty:
            risk_parts.append("存在冲突")
        if breakdown.missing_evidence_penalty:
            risk_parts.append("证据缺口")
        if breakdown.excluded:
            risk_parts.append("命中限制")
        lines.append(
            f"| {item.module_name} | {item.score} | {_score_level(item.score)} | "
            f"{breakdown.positive_score} | {breakdown.negative_score} | {'、'.join(risk_parts) or '无明显风险'} |"
        )
    lines.append("")
    summary = rec.scoring_summary
    lines.append(
        f"评分规则共评估 {summary.rules_evaluated} 条，命中 {summary.rules_matched} 条；"
        f"排除/限制产品 {summary.products_excluded} 个。"
    )
    lines.append("")
    return lines


def _score_level(score: int) -> str:
    if score >= HIGH_SCORE_THRESHOLD:
        return "强推荐"
    if score >= MEDIUM_SCORE_THRESHOLD:
        return "可跟进"
    if score >= LOW_SCORE_THRESHOLD:
        return "观察"
    return "暂不建议"
