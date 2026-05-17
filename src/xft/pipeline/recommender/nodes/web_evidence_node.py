"""Merge DuckDB unified evidence into dimension analysis via resolver."""

from __future__ import annotations

from typing import Any, Literal

import duckdb
import structlog

from xft.evidence.models import EvidenceRecord
from xft.evidence.repository import EvidenceRepository
from xft.evidence.resolver import ResolvedDimensionEvidence, resolve_dimension_evidence
from xft.pipeline.recommender.models import DimensionAnalysis, EvidenceFact
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display

MAX_WEB_EVIDENCE_PER_DIMENSION = 5
SUPPORTED_QUALITY_THRESHOLD = 45
PARTIAL_QUALITY_THRESHOLD = 15
HIGH_CONFIDENCE_QUALITY_THRESHOLD = 60
MEDIUM_CONFIDENCE_QUALITY_THRESHOLD = 30
LOW_CONFIDENCE_QUALITY_THRESHOLD = 10

log = structlog.get_logger(__name__)


async def web_evidence_node(state: RecommenderState) -> dict[str, object]:
    """Resolve and merge all unified evidence into dimension analysis.

    When use_web_evidence is enabled, this node queries the unified_evidence
    table (local_json + web + rule), resolves conflicts via the evidence
    resolver, and overwrites the dimension analysis with the resolved package.
    """
    enabled = state.get("use_web_evidence", False)
    profile = state.get("profile", {})
    company_name = str(profile.get("company_name") or state["company_name"])
    credit_code = profile.get("credit_code")

    if not enabled:
        display.skip("Web 证据: 未启用")
        log.info("web_evidence_node_skipped", company_name=company_name, reason="use_web_evidence=False")
        return {}

    display.phase(3, 5, "Web 证据采集")
    log.info("web_evidence_node_start", company_name=company_name)

    try:
        all_evidence = _fetch_unified_evidence(
            state["warehouse_db"], company_name=company_name, credit_code=credit_code
        )
    except (duckdb.Error, OSError, RuntimeError, ValueError):
        log.warning("web_evidence_node_fetch_error", company_name=company_name, exc_info=True)
        all_evidence = []

    if not all_evidence:
        display.skip("unified_evidence 表中无该企业记录")
        log.info(
            "web_evidence_node_no_evidence",
            company_name=company_name,
            reason="unified_evidence 表中无该企业记录",
        )
        return {}

    # Group evidence by dimension
    by_dim: dict[str, list[EvidenceRecord]] = {}
    for ev in all_evidence:
        dim_id = ev.dimension_id
        if dim_id:
            by_dim.setdefault(dim_id, []).append(ev)

    display.ok(f"unified_evidence 表 → {len(all_evidence)} 条证据 → {len(by_dim)} 个维度")

    log.info(
        "web_evidence_node_fetched",
        company_name=company_name,
        total_evidence=len(all_evidence),
        dimensions_with_evidence=len(by_dim),
    )

    enriched: list[DimensionAnalysis] = []
    for analysis in state["dimension_analysis"]:
        dim_evidence = by_dim.get(analysis.dimension_id, [])

        if not dim_evidence:
            enriched.append(analysis)
            continue

        resolved = resolve_dimension_evidence(
            dim_evidence,
            missing_fields=analysis.missing_evidence,
        )
        log.info(
            "web_evidence_node_dimension_resolved",
            dimension_id=analysis.dimension_id,
            primary=len(resolved.primary_evidence),
            supplement=len(resolved.supplement_evidence),
            confirmation=len(resolved.confirmation_evidence),
            conflict=len(resolved.conflict_evidence),
            inference=len(resolved.inference_evidence),
            quality_score=resolved.quality_score,
        )
        web_count = len([e for e in resolved.primary_evidence if e.source_type != "local_json"])
        web_count += len(resolved.supplement_evidence) + len(resolved.confirmation_evidence)
        conflicts = len(resolved.conflict_evidence)
        score = resolved.quality_score
        quality_label = (
            "high"
            if score >= HIGH_CONFIDENCE_QUALITY_THRESHOLD
            else "medium"
            if score >= MEDIUM_CONFIDENCE_QUALITY_THRESHOLD
            else "low"
        )
        detail = f"质量 {score:.0f} ({quality_label})"
        if conflicts:
            detail += f" ⚠ {conflicts}处冲突"
        display.branch(f"{analysis.dimension_id}: {web_count}条Web → {detail}")
        enriched.append(_merge_resolved(analysis, resolved))

    return {"dimension_analysis": enriched}


def _fetch_unified_evidence(
    warehouse_db: str,
    *,
    company_name: str,
    credit_code: Any,
) -> list[EvidenceRecord]:
    """Fetch all unified evidence for a company (all source types)."""
    repo = EvidenceRepository(warehouse_db)
    return repo.get_company_evidence(
        company_name=company_name,
        credit_code=str(credit_code) if credit_code else None,
    )


def _merge_resolved(
    analysis: DimensionAnalysis,
    resolved: ResolvedDimensionEvidence,
) -> DimensionAnalysis:
    """Overwrite dimension analysis fields with resolved evidence."""
    # Rebuild facts from primary evidence
    primary_facts = [
        EvidenceFact(
            claim=ev.claim,
            source=ev.source_name or ev.source_type,
            source_fields=[ev.source_field] if ev.source_field else [],
        )
        for ev in resolved.primary_evidence[:6]
    ]

    # Local evidence = primary items from local_json
    local_evs = [ev for ev in resolved.primary_evidence if ev.source_type == "local_json"]

    # Web evidence = primary_from_web + supplement + confirmation (excluding conflicts)
    primary_from_web = [ev for ev in resolved.primary_evidence if ev.source_type != "local_json"]
    web_evs = (
        primary_from_web[:MAX_WEB_EVIDENCE_PER_DIMENSION]
        + resolved.supplement_evidence[:MAX_WEB_EVIDENCE_PER_DIMENSION]
        + resolved.confirmation_evidence[:MAX_WEB_EVIDENCE_PER_DIMENSION]
    )

    # Infer status from resolved quality
    status: Literal["supported", "partial", "insufficient"]
    if resolved.quality_score >= SUPPORTED_QUALITY_THRESHOLD:
        status = "supported"
    elif resolved.quality_score >= PARTIAL_QUALITY_THRESHOLD:
        status = "partial"
    else:
        status = "insufficient"

    confidence: Literal["高", "中", "低", "待补充"]
    if resolved.quality_score >= HIGH_CONFIDENCE_QUALITY_THRESHOLD:
        confidence = "高"
    elif resolved.quality_score >= MEDIUM_CONFIDENCE_QUALITY_THRESHOLD:
        confidence = "中"
    elif resolved.quality_score >= LOW_CONFIDENCE_QUALITY_THRESHOLD:
        confidence = "低"
    else:
        confidence = "待补充"

    return analysis.model_copy(
        update={
            "facts": primary_facts or analysis.facts,
            "local_evidence": local_evs or analysis.local_evidence,
            "inference_evidence": resolved.inference_evidence or analysis.inference_evidence,
            "web_evidence": web_evs,
            "conflicts": resolved.conflict_evidence,
            "missing_evidence": resolved.missing_evidence or analysis.missing_evidence,
            "status": status,
            "confidence": confidence,
        }
    )
