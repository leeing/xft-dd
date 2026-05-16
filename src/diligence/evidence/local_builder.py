"""Builders that convert local profile facts and rules into unified evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from diligence.evidence.models import EvidenceRecord


def make_evidence_id(*parts: object) -> str:
    """Build a stable short evidence id from semantic parts."""
    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"ev_{digest}"


def build_local_evidence(
    *,
    profile: dict[str, Any],
    dimension_id: str,
    claim: str,
    source_field: str,
    value: Any,
) -> EvidenceRecord:
    """Create a primary local JSON/profile evidence item."""
    company_name = str(profile.get("company_name") or "")
    credit_code = str(profile.get("credit_code") or "") or None
    return EvidenceRecord(
        evidence_id=make_evidence_id(credit_code or company_name, dimension_id, source_field, claim),
        credit_code=credit_code,
        company_name=company_name,
        dimension_id=dimension_id,
        source_type="local_json",
        source_name="company_profile",
        source_field=source_field,
        claim=claim,
        value=str(value),
        confidence="中",
        authority_level="high",
        relation_to_profile="primary",
    )


def build_rule_evidence(
    *,
    profile: dict[str, Any],
    dimension_id: str,
    claim: str,
    source_field: str | None = None,
) -> EvidenceRecord:
    """Create a rule-derived inference evidence item."""
    company_name = str(profile.get("company_name") or "")
    credit_code = str(profile.get("credit_code") or "") or None
    return EvidenceRecord(
        evidence_id=make_evidence_id(credit_code or company_name, dimension_id, source_field or "rule", claim),
        credit_code=credit_code,
        company_name=company_name,
        dimension_id=dimension_id,
        source_type="rule",
        source_name="dimension_support_rules",
        source_field=source_field,
        claim=claim,
        confidence="低",
        authority_level="unknown",
        relation_to_profile="inference",
    )
