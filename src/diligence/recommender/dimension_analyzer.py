"""Map a local company profile into configured analysis dimensions."""

from __future__ import annotations

from typing import Any, Literal

from diligence.recommender.models import AnalysisDimension, DimensionAnalysis, EvidenceFact

SUPPORTED_FACTS_THRESHOLD = 3
SUPPLY_CHAIN_EMPLOYEE_THRESHOLD = 200
HR_EMPLOYEE_THRESHOLD = 100
TECH_IP_THRESHOLD = 20
RISK_COUNT_THRESHOLD = 20
DIGITAL_EMPLOYEE_THRESHOLD = 300
DIGITAL_BRANCH_THRESHOLD = 3


def _get_nested(profile: dict[str, Any], path: str) -> Any:
    cur: Any = profile
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _has_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return True


def _format_value(value: Any) -> str:
    if isinstance(value, dict):
        nonzero = {k: v for k, v in value.items() if _has_value(v)}
        return "、".join(f"{k}:{v}" for k, v in list(nonzero.items())[:8]) or "无明显记录"
    if isinstance(value, list):
        return "、".join(str(item) for item in value[:8]) or "无明显记录"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def analyze_dimensions(
    *,
    profile: dict[str, Any],
    dimensions: list[AnalysisDimension],
) -> list[DimensionAnalysis]:
    """Generate local, source-aware dimension analysis without web search."""
    results: list[DimensionAnalysis] = []
    for dim in dimensions:
        facts: list[EvidenceFact] = []
        for template in dim.evidence_templates:
            value = _get_nested(profile, template.field)
            if not _has_value(value):
                continue
            facts.append(
                EvidenceFact(
                    claim=f"{template.label}：{_format_value(value)}",
                    source_fields=[template.field],
                )
            )
        status: Literal["supported", "partial", "insufficient"]
        status = "supported" if len(facts) >= SUPPORTED_FACTS_THRESHOLD else "partial" if facts else "insufficient"
        confidence: Literal["高", "中", "低", "待补充"]
        confidence = "中" if status == "supported" else "低" if status == "partial" else "待补充"
        inferences = _build_inferences(dim.id, profile)
        results.append(
            DimensionAnalysis(
                dimension_id=dim.id,
                title=dim.title,
                status=status,
                confidence=confidence,
                facts=facts,
                inferences=inferences,
                missing_evidence=dim.insufficient_evidence,
            )
        )
    return results


def _build_inferences(dimension_id: str, profile: dict[str, Any]) -> list[str]:
    employee_count = profile.get("employee_count") or 0
    industry = str(profile.get("industry") or "")
    raw_ip_counts = profile.get("ip_counts")
    raw_risk_counts = profile.get("risk_counts")
    ip_counts: dict[str, Any] = raw_ip_counts if isinstance(raw_ip_counts, dict) else {}
    risk_counts: dict[str, Any] = raw_risk_counts if isinstance(raw_risk_counts, dict) else {}
    inferences: list[str] = []
    if (
        dimension_id == "supply_chain_procurement"
        and "制造" in industry
        and employee_count >= SUPPLY_CHAIN_EMPLOYEE_THRESHOLD
    ):
        inferences.append("基于制造业属性和员工规模，弱推测存在一定采购协同与供应链管理复杂度。")
    if dimension_id == "hr_workforce" and employee_count >= HR_EMPLOYEE_THRESHOLD:
        inferences.append("基于员工规模，弱推测存在组织人事、考勤和薪酬管理需求。")
    if dimension_id == "tech_innovation" and sum(int(v or 0) for v in ip_counts.values()) >= TECH_IP_THRESHOLD:
        inferences.append("基于知识产权数量，推断企业具备一定研发或技术资产管理需求。")
    if dimension_id == "compliance_risk" and sum(int(v or 0) for v in risk_counts.values()) >= RISK_COUNT_THRESHOLD:
        inferences.append("基于风险记录数量，推断企业存在风险台账和合规跟踪需求。")
    if dimension_id == "digitalization" and (
        employee_count >= DIGITAL_EMPLOYEE_THRESHOLD or (profile.get("branch_count") or 0) >= DIGITAL_BRANCH_THRESHOLD
    ):
        inferences.append("基于规模或多组织特征，弱推测存在系统协同和经营数据整合需求。")
    return inferences
