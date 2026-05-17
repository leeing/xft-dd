"""Evidence resolver: merge, deduplicate, and resolve conflicts across sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xft.evidence.models import EvidenceRecord
from xft.evidence.policy import EvidencePolicy, ResolverPolicy
from xft.utils.source_registry import classify_source

Confidence = Literal["高", "中", "低", "待补充", "待核实"]


@dataclass
class ResolvedDimensionEvidence:
    """Resolved evidence package for one analysis dimension."""

    dimension_id: str
    primary_evidence: list[EvidenceRecord] = field(default_factory=list)
    supplement_evidence: list[EvidenceRecord] = field(default_factory=list)
    confirmation_evidence: list[EvidenceRecord] = field(default_factory=list)
    conflict_evidence: list[EvidenceRecord] = field(default_factory=list)
    inference_evidence: list[EvidenceRecord] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    quality_score: float = 0.0


# Confidence ordering for ranking
_CONFIDENCE_ORDER: dict[str, int] = {"高": 0, "中": 1, "低": 2, "待核实": 3, "待补充": 4}
_DEFAULT_RESOLVER_POLICY = ResolverPolicy()


def resolve_dimension_evidence(
    evidence_list: list[EvidenceRecord],
    *,
    missing_fields: list[str] | None = None,
    policy: EvidencePolicy | ResolverPolicy | None = None,
) -> ResolvedDimensionEvidence:
    """Merge and resolve conflicts for evidence items within one dimension.

    Resolution rules:
    1. Deduplicate by (claim, source_type, source_field).
    2. Apply source authority boost from source_registry.
    3. Group by source_field. Local/manual/rule win over web in conflicts.
    4. Web confirmation is kept separately and can elevate local confidence.
    5. Web conflict is marked with resolution=use_local and excluded from primary.
    6. If no primary evidence exists, best web supplement is promoted to primary.
    """
    if not evidence_list:
        return ResolvedDimensionEvidence(
            dimension_id="",
            missing_evidence=list(missing_fields or []),
        )

    dimension_id = evidence_list[0].dimension_id or ""

    # Step 1: Deduplicate
    deduped = _deduplicate(evidence_list)

    # Step 2: Apply source authority boost
    resolver_policy = _resolver_policy(policy)
    boosted = [_apply_source_boost(ev, resolver_policy) for ev in deduped]

    # Step 3: Separate by source type and relation
    primary: list[EvidenceRecord] = []
    supplement: list[EvidenceRecord] = []
    confirmation: list[EvidenceRecord] = []
    conflicts: list[EvidenceRecord] = []
    inferences: list[EvidenceRecord] = []

    # Group by source_field for conflict detection
    by_field: dict[str | None, list[EvidenceRecord]] = {}
    for ev in boosted:
        by_field.setdefault(ev.source_field, []).append(ev)

    for field_evs in by_field.values():
        _process_field_group(field_evs, primary, supplement, confirmation, conflicts, inferences, resolver_policy)

    # If no primary at all, promote best web evidence to primary
    if not primary and (supplement or confirmation):
        best = _pick_best_evidence(supplement + confirmation)
        if best:
            primary.append(best.model_copy(update={"relation_to_profile": "primary"}))
            # Remove from original list to avoid duplication
            if best in supplement:
                supplement.remove(best)
            elif best in confirmation:
                confirmation.remove(best)

    # Compute quality score
    quality = _compute_quality_score(primary, supplement, confirmation, conflicts, inferences, resolver_policy)

    return ResolvedDimensionEvidence(
        dimension_id=dimension_id,
        primary_evidence=primary,
        supplement_evidence=supplement,
        confirmation_evidence=confirmation,
        conflict_evidence=conflicts,
        inference_evidence=inferences,
        missing_evidence=list(missing_fields or []),
        quality_score=quality,
    )


def _process_field_group(  # noqa: PLR0913
    field_evs: list[EvidenceRecord],
    primary: list[EvidenceRecord],
    supplement: list[EvidenceRecord],
    confirmation: list[EvidenceRecord],
    conflicts: list[EvidenceRecord],
    inferences: list[EvidenceRecord],
    policy: ResolverPolicy,
) -> None:
    """Classify evidence within one source_field group and append to result lists."""
    sorted_evs = sorted(field_evs, key=lambda ev: policy.source_priority.get(ev.source_type, 99))
    local_items = [ev for ev in sorted_evs if ev.source_type == "local_json"]
    web_items = [ev for ev in sorted_evs if ev.source_type == "web"]
    rule_items = [ev for ev in sorted_evs if ev.source_type == "rule"]
    other_items = [ev for ev in sorted_evs if ev.source_type not in ("local_json", "web", "rule")]

    # Local and manual/other primary sources
    for ev in local_items + other_items:
        if ev.relation_to_profile == "inference":
            inferences.append(ev)
        else:
            primary.append(ev)

    # Rule inferences
    inferences.extend(rule_items)

    # Web items: check for conflicts against local
    for ev in web_items:
        if ev.relation_to_profile == "conflict":
            conflicts.append(ev.model_copy(update={"resolution": "use_local"}))
        elif ev.relation_to_profile == "confirmation":
            confirmation.append(ev)
        elif local_items:
            supplement.append(ev)
        else:
            primary.append(ev.model_copy(update={"relation_to_profile": "primary"}))


def _deduplicate(evidence_list: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Remove exact duplicates by (claim, source_type, source_field)."""
    seen: set[str] = set()
    result: list[EvidenceRecord] = []
    for ev in evidence_list:
        key = f"{ev.claim}|{ev.source_type}|{ev.source_field or ''}|{ev.source_url or ''}"
        if key in seen:
            continue
        seen.add(key)
        result.append(ev)
    return result


def _apply_source_boost(ev: EvidenceRecord, policy: ResolverPolicy | None = None) -> EvidenceRecord:
    """Boost confidence for high-authority web sources."""
    policy = policy or _DEFAULT_RESOLVER_POLICY
    if ev.source_type in ("local_json", "manual"):
        return ev

    url = ev.source_url
    if not url:
        return ev

    info = classify_source(url)

    if info.authority_level in policy.authority_boost:
        new_confidence = policy.authority_boost[info.authority_level].get(ev.confidence)
        if new_confidence:
            return ev.model_copy(update={"confidence": new_confidence})

    return ev


def _pick_best_evidence(evidence_list: list[EvidenceRecord]) -> EvidenceRecord | None:
    """Return the highest-confidence evidence item."""
    if not evidence_list:
        return None
    return min(evidence_list, key=lambda ev: _CONFIDENCE_ORDER.get(ev.confidence, 5))


def _compute_quality_score(  # noqa: PLR0913
    primary: list[EvidenceRecord],
    supplement: list[EvidenceRecord],
    confirmation: list[EvidenceRecord],
    conflicts: list[EvidenceRecord],
    inferences: list[EvidenceRecord],
    policy: ResolverPolicy | None = None,
) -> float:
    """Compute a dimension evidence quality score (0-100)."""
    weights = (policy or _DEFAULT_RESOLVER_POLICY).quality_score
    score = 0.0
    score += len(primary) * weights.primary
    score += len(confirmation) * weights.confirmation
    score += len(supplement) * weights.supplement
    score += len(inferences) * weights.inference
    score -= len(conflicts) * weights.conflict_penalty
    return max(weights.min_score, min(weights.max_score, score))


def _resolver_policy(policy: EvidencePolicy | ResolverPolicy | None) -> ResolverPolicy:
    if isinstance(policy, EvidencePolicy):
        return policy.resolver
    if policy is not None:
        return policy
    return _DEFAULT_RESOLVER_POLICY
