"""Optionally merge DuckDB Web evidence into dimension analysis."""

from __future__ import annotations

from typing import Any, Literal

import duckdb

from diligence.evidence.models import EvidenceRecord
from diligence.recommender.models import DimensionAnalysis
from diligence.recommender.state import RecommenderState

MAX_WEB_EVIDENCE_PER_DIMENSION = 5


async def web_evidence_node(state: RecommenderState) -> dict[str, object]:
    """Append cached Web evidence to dimension inferences when enabled."""
    if not state.get("use_web_evidence", False):
        return {}
    profile = state.get("profile", {})
    company_name = str(profile.get("company_name") or state["company_name"])
    credit_code = profile.get("credit_code")
    try:
        rows = _fetch_web_evidence(state["warehouse_db"], company_name=company_name, credit_code=credit_code)
    except (duckdb.Error, OSError, RuntimeError, ValueError):
        return {}
    if not rows:
        return {}
    by_dim: dict[str, list[EvidenceRecord]] = {}
    for row in rows:
        by_dim.setdefault(str(row["dimension_id"]), []).append(_row_to_evidence(row, company_name=company_name))
    enriched: list[DimensionAnalysis] = []
    for analysis in state["dimension_analysis"]:
        web_items = by_dim.get(analysis.dimension_id, [])[:MAX_WEB_EVIDENCE_PER_DIMENSION]
        if not web_items:
            enriched.append(analysis)
            continue
        conflicts = [item for item in web_items if item.relation_to_profile == "conflict"]
        enriched.append(
            analysis.model_copy(
                update={
                    "web_evidence": [*analysis.web_evidence, *web_items],
                    "conflicts": [*analysis.conflicts, *conflicts],
                }
            )
        )
    return {"dimension_analysis": enriched}


def _fetch_web_evidence(
    warehouse_db: str,
    *,
    company_name: str,
    credit_code: Any,
) -> list[dict[str, Any]]:
    conn = duckdb.connect(warehouse_db, read_only=True)
    try:
        where = "credit_code = ?" if credit_code else "company_name = ?"
        param = str(credit_code or company_name)
        try:
            result = conn.execute(
                f"""
                SELECT evidence_id, dimension_id, claim, relation_to_profile AS evidence_type,
                       relation_to_profile, confidence, NULL AS source_title, source_url,
                       source_name AS provider, NULL AS query, conflict_note, resolution, created_at
                FROM unified_evidence
                WHERE source_type = 'web' AND {where}
                ORDER BY created_at DESC
                """,  # noqa: S608
                [param],
            )
            columns = [desc[0] for desc in result.description]
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
            if rows:
                return rows
        except duckdb.Error:
            pass
        result = conn.execute(
            f"""
            SELECT evidence_id, dimension_id, claim, evidence_type, relation_to_profile, confidence,
                   source_title, source_url, provider, query, conflict_note, resolution, created_at
            FROM web_evidence
            WHERE {where}
            ORDER BY created_at DESC
            """,  # noqa: S608
            [param],
        )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        conn.close()


def _row_to_evidence(row: dict[str, Any], *, company_name: str) -> EvidenceRecord:
    evidence_type = row.get("evidence_type")
    relation: Literal["supplement", "confirmation", "conflict"]
    raw_relation = row.get("relation_to_profile") or evidence_type or "supplement"
    if raw_relation == "confirmation":
        relation = "confirmation"
    elif raw_relation == "conflict":
        relation = "conflict"
    else:
        relation = "supplement"
    confidence: Literal["高", "中", "低", "待补充", "待核实"]
    raw_confidence = row.get("confidence")
    if raw_confidence == "高":
        confidence = "高"
    elif raw_confidence == "中":
        confidence = "中"
    elif raw_confidence == "待补充":
        confidence = "待补充"
    elif raw_confidence == "待核实":
        confidence = "待核实"
    else:
        confidence = "低"
    resolution: Literal["use_local"] | None = "use_local" if relation == "conflict" else None
    return EvidenceRecord(
        evidence_id=str(row["evidence_id"]),
        company_name=company_name,
        dimension_id=str(row["dimension_id"]),
        source_type="web",
        source_name=str(row.get("provider") or "web"),
        source_url=row.get("source_url"),
        claim=str(row["claim"]),
        confidence=confidence,
        authority_level="unknown",
        relation_to_profile=relation,
        conflict_note=row.get("conflict_note"),
        resolution=resolution,
        raw_ref={
            "source_title": row.get("source_title"),
            "query": row.get("query"),
            "created_at": row.get("created_at"),
            "resolution": row.get("resolution"),
        },
    )
