"""Unified evidence records consumed by analysis and recommendation layers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

EvidenceSourceType = Literal["local_json", "web", "manual", "llm_extraction", "rule"]
EvidenceRelation = Literal["primary", "supplement", "confirmation", "conflict", "inference"]
EvidenceConfidence = Literal["高", "中", "低", "待补充", "待核实"]
AuthorityLevel = Literal["high", "medium", "low", "unknown"]
EvidenceResolution = Literal["use_local", "use_web", "manual_review"]


class EvidenceRecord(BaseModel):
    """A normalized evidence item from JSON, Web, rules, or manual review."""

    evidence_id: str
    credit_code: str | None = None
    company_name: str
    dimension_id: str | None = None
    source_type: EvidenceSourceType
    source_name: str
    source_path: str | None = None
    source_url: str | None = None
    source_field: str | None = None
    claim: str
    value: str | None = None
    confidence: EvidenceConfidence = "低"
    authority_level: AuthorityLevel = "unknown"
    relation_to_profile: EvidenceRelation
    conflict_note: str | None = None
    resolution: EvidenceResolution | None = None
    raw_ref: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
