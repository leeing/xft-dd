"""Query unified_evidence from the DuckDB warehouse."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import duckdb

from diligence.evidence.models import (
    AuthorityLevel,
    EvidenceConfidence,
    EvidenceRecord,
    EvidenceRelation,
    EvidenceSourceType,
    normalize_resolution,
)


@dataclass(frozen=True)
class EvidenceQueryFilter:
    """Filter criteria for unified_evidence queries."""

    company_name: str | None = None
    credit_code: str | None = None
    dimension_id: str | None = None
    source_type: str | None = None
    relation_to_profile: str | None = None


class EvidenceRepository:
    """Read unified_evidence from DuckDB with structured filtering."""

    def __init__(self, warehouse_db: str | Path):
        self.warehouse_db = str(warehouse_db)

    def get_company_evidence(
        self,
        company_name: str,
        *,
        credit_code: str | None = None,
        source_type: str | None = None,
    ) -> list[EvidenceRecord]:
        """Return all evidence for a company, optionally filtered by source_type."""
        return self._query(
            EvidenceQueryFilter(
                company_name=company_name,
                credit_code=credit_code,
                source_type=source_type,
            )
        )

    def get_dimension_evidence(
        self,
        company_name: str,
        dimension_id: str,
        *,
        credit_code: str | None = None,
    ) -> list[EvidenceRecord]:
        """Return evidence for a specific dimension."""
        return self._query(
            EvidenceQueryFilter(
                company_name=company_name,
                credit_code=credit_code,
                dimension_id=dimension_id,
            )
        )

    def get_conflicts(
        self,
        company_name: str,
        *,
        credit_code: str | None = None,
    ) -> list[EvidenceRecord]:
        """Return evidence items marked as conflict."""
        return self._query(
            EvidenceQueryFilter(
                company_name=company_name,
                credit_code=credit_code,
                relation_to_profile="conflict",
            )
        )

    def get_missing_evidence_summary(
        self,
        company_name: str,
        *,
        credit_code: str | None = None,
    ) -> dict[str, list[str]]:
        """Return a map of dimension_id -> list of missing evidence descriptions.

        This is derived from the local_json evidence records that have
        a special marker in raw_ref indicating they represent a missing field.
        """
        rows = self._query(
            EvidenceQueryFilter(
                company_name=company_name,
                credit_code=credit_code,
            )
        )
        missing: dict[str, list[str]] = {}
        for row in rows:
            dim = row.dimension_id
            if dim and row.raw_ref.get("missing"):
                missing.setdefault(dim, []).append(row.claim)
        return missing

    def _query(self, filter_obj: EvidenceQueryFilter) -> list[EvidenceRecord]:
        conn = duckdb.connect(self.warehouse_db, read_only=True)
        try:
            conditions: list[str] = []
            params: list[Any] = []

            if filter_obj.company_name:
                conditions.append("company_name = ?")
                params.append(filter_obj.company_name)
            if filter_obj.credit_code:
                conditions.append("credit_code = ?")
                params.append(filter_obj.credit_code)
            if filter_obj.dimension_id:
                conditions.append("dimension_id = ?")
                params.append(filter_obj.dimension_id)
            if filter_obj.source_type:
                conditions.append("source_type = ?")
                params.append(filter_obj.source_type)
            if filter_obj.relation_to_profile:
                conditions.append("relation_to_profile = ?")
                params.append(filter_obj.relation_to_profile)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = f"""
                SELECT
                    evidence_id, credit_code, company_name, dimension_id,
                    source_type, source_name, source_path, source_url, source_field,
                    claim, value, confidence, authority_level, relation_to_profile,
                    conflict_note, resolution, raw_ref, created_at
                FROM unified_evidence
                WHERE {where_clause}
                ORDER BY created_at DESC
            """  # noqa: S608
            result = conn.execute(sql, params)
            columns = [desc[0] for desc in result.description]
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
            return [_row_to_record(row) for row in rows]
        finally:
            conn.close()


def _s(value: Any, default: str = "") -> str:
    """Safe str cast from duckdb row values."""
    return str(value) if value is not None else default


def _row_to_record(row: dict[str, Any]) -> EvidenceRecord:
    raw_ref = row.get("raw_ref")
    if isinstance(raw_ref, str):
        try:
            raw_ref = json.loads(raw_ref)
        except json.JSONDecodeError:
            raw_ref = {}
    if not isinstance(raw_ref, dict):
        raw_ref = {}
    return EvidenceRecord(
        evidence_id=_s(row.get("evidence_id")),
        credit_code=row.get("credit_code"),
        company_name=_s(row.get("company_name")),
        dimension_id=row.get("dimension_id"),
        source_type=cast(EvidenceSourceType, _s(row.get("source_type"))),
        source_name=_s(row.get("source_name")),
        source_path=row.get("source_path"),
        source_url=row.get("source_url"),
        source_field=row.get("source_field"),
        claim=_s(row.get("claim")),
        value=row.get("value"),
        confidence=cast(EvidenceConfidence, _s(row.get("confidence"), default="低")),
        authority_level=cast(AuthorityLevel, _s(row.get("authority_level"), default="unknown")),
        relation_to_profile=cast(EvidenceRelation, _s(row.get("relation_to_profile"), default="primary")),
        conflict_note=row.get("conflict_note"),
        resolution=normalize_resolution(row.get("resolution")),
        raw_ref=raw_ref,
        created_at=row.get("created_at") or datetime.now(UTC),
    )
