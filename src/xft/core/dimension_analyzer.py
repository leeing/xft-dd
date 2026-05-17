"""Map a local company profile into configured analysis dimensions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from xft.core.models import AnalysisDimension, DimensionAnalysis, EvidenceFact, SupportRule
from xft.display import format_profile_value
from xft.evidence.local_builder import build_local_evidence, build_rule_evidence
from xft.evidence.models import EvidenceRecord

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
    if isinstance(value, int | float):
        return value != 0
    return True


def analyze_dimensions(
    *,
    profile: dict[str, Any],
    dimensions: list[AnalysisDimension],
) -> list[DimensionAnalysis]:
    """Generate local, source-aware dimension analysis without web search."""
    results: list[DimensionAnalysis] = []
    for dim in dimensions:
        facts: list[EvidenceFact] = []
        local_evidence: list[EvidenceRecord] = []
        for template in dim.evidence_templates:
            value = _get_nested(profile, template.field)
            if not _has_value(value):
                continue
            claim = f"{template.label}：{format_profile_value(value, field=template.field)}"
            facts.append(
                EvidenceFact(
                    claim=claim,
                    source_fields=[template.field],
                )
            )
            local_evidence.append(
                build_local_evidence(
                    profile=profile,
                    dimension_id=dim.id,
                    claim=claim,
                    source_field=template.field,
                    value=value,
                )
            )
        status: Literal["supported", "partial", "insufficient"]
        status = "supported" if len(facts) >= SUPPORTED_FACTS_THRESHOLD else "partial" if facts else "insufficient"
        confidence: Literal["高", "中", "低", "待补充"]
        confidence = "中" if status == "supported" else "低" if status == "partial" else "待补充"
        inferences = _build_rule_inferences(dim.support_rules, profile)
        inference_evidence = [
            build_rule_evidence(profile=profile, dimension_id=dim.id, claim=claim) for claim in inferences
        ]
        if not inferences:
            inferences = _build_legacy_inferences(dim.id, profile)
            inference_evidence = [
                build_rule_evidence(profile=profile, dimension_id=dim.id, claim=claim) for claim in inferences
            ]
        results.append(
            DimensionAnalysis(
                dimension_id=dim.id,
                title=dim.title,
                status=status,
                confidence=confidence,
                facts=facts,
                inferences=inferences,
                local_evidence=local_evidence,
                inference_evidence=inference_evidence,
                missing_evidence=dim.insufficient_evidence,
                analysis_prompt=dim.analysis_prompt,
                evidence_policy=dim.evidence_policy,
                web_search_queries=_render_queries(dim.web_search_queries, profile),
            )
        )
    return results


def _build_rule_inferences(rules: list[SupportRule], profile: dict[str, Any]) -> list[str]:
    inferences: list[str] = []
    for rule in rules:
        value = _get_nested(profile, rule.field)
        if _rule_matches(value, rule):
            inferences.append(rule.claim)
    return inferences


def _rule_matches(value: Any, rule: SupportRule) -> bool:
    if rule.op == "exists":
        return _has_value(value)
    if not _has_value(value):
        return False
    matchers: dict[str, Callable[[], bool]] = {
        "contains": lambda: _contains(value, rule.value),
        ">": lambda: _compare_numbers(value, rule.value, rule.op),
        ">=": lambda: _compare_numbers(value, rule.value, rule.op),
        "<": lambda: _compare_numbers(value, rule.value, rule.op),
        "<=": lambda: _compare_numbers(value, rule.value, rule.op),
        "==": lambda: value == rule.value,
        "!=": lambda: value != rule.value,
    }
    matcher = matchers.get(rule.op)
    return bool(matcher()) if matcher else False


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, expected) for item in value)
    return str(expected) in str(value)


def _compare_numbers(value: Any, expected: Any, op: str) -> bool:
    try:
        left = float(value)
        right = float(expected)
    except (TypeError, ValueError):
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


def _render_queries(queries: list[str], profile: dict[str, Any]) -> list[str]:
    company_name = str(profile.get("company_name") or "")
    industry = str(profile.get("industry") or "")
    industry_big = str(profile.get("industry_big") or "")
    return [query.format(company_name=company_name, industry=industry, industry_big=industry_big) for query in queries]


def _build_legacy_inferences(dimension_id: str, profile: dict[str, Any]) -> list[str]:
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
