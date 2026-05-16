"""Markdown report rendering for recommender outputs."""

from __future__ import annotations

from diligence.recommender.state import RecommenderState


def render_report(state: RecommenderState) -> str:
    """Render a concise Markdown recommendation report."""
    return "\n".join(
        [
            *_render_profile_summary(state),
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


def _render_dimension_summary(state: RecommenderState) -> list[str]:
    lines = ["## 维度分析摘要", ""]
    for analysis in state["dimension_analysis"]:
        facts = "；".join(fact.claim for fact in analysis.facts[:3]) or "本地数据不足"
        lines.append(f"- {analysis.title}：{analysis.status}，{analysis.confidence}。{facts}")
    lines.append("")
    return lines


def _render_recommendations(state: RecommenderState) -> list[str]:
    rec = state["recommendation"]
    lines = ["## 推荐模块", ""]
    if rec is None:
        lines.append("推荐结果生成失败。")
        return lines
    lines.extend([rec.summary, ""])
    for rec_item in rec.recommendations:
        lines.extend(
            [
                f"### {rec_item.rank}. {rec_item.module_name}",
                "",
                f"- 推荐分：{rec_item.score}",
                f"- 业务需求：{rec_item.business_need}",
                f"- 推荐理由：{rec_item.reason}",
                f"- 建议话术：{rec_item.suggested_pitch}",
                f"- 证据维度：{'；'.join(rec_item.evidence_dimensions) or '本地证据不足'}",
                f"- 待补充：{'；'.join(rec_item.data_gaps) or '无'}",
                "",
            ]
        )
    return lines

