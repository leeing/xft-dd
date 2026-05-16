"""Persist recommender outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diligence.recommender.state import RecommenderState


def _json_default(value: Any) -> str:
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _render_report(state: RecommenderState) -> str:
    rec = state["recommendation"]
    profile = state.get("profile", {})
    lines = [
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
        "## 维度分析摘要",
        "",
    ]
    for analysis in state["dimension_analysis"]:
        facts = "；".join(fact.claim for fact in analysis.facts[:3]) or "本地数据不足"
        lines.append(f"- {analysis.title}：{analysis.status}，{analysis.confidence}。{facts}")
    lines.extend(["", "## 推荐模块", ""])
    if rec is None:
        lines.append("推荐结果生成失败。")
        return "\n".join(lines)
    lines.append(rec.summary)
    lines.append("")
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
    return "\n".join(lines)


async def save_node(state: RecommenderState) -> dict[str, object]:
    out_dir = Path(state["output_root"]) / state["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_path = out_dir / "profile.json"
    dimensions_path = out_dir / "dimension_analysis.json"
    matches_path = out_dir / "match_results.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    _write_json(profile_path, state.get("profile", {}))
    _write_json(dimensions_path, [item.model_dump() for item in state["dimension_analysis"]])
    _write_json(matches_path, [item.model_dump() for item in state["match_results"]])
    rec = state["recommendation"]
    _write_json(result_path, rec.model_dump() if rec else {"error": "recommendation not generated"})
    report_path.write_text(_render_report(state), encoding="utf-8")

    return {
        "output_dir": str(out_dir),
        "report_path": str(report_path),
        "result_path": str(result_path),
    }
