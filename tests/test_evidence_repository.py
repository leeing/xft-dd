"""Tests for evidence.repository querying unified_evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from xft.evidence.repository import EvidenceRepository

SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_evidence (
    evidence_id TEXT PRIMARY KEY,
    credit_code TEXT,
    company_name TEXT NOT NULL,
    dimension_id TEXT,
    source_type TEXT NOT NULL,
    source_name TEXT,
    source_path TEXT,
    source_url TEXT,
    source_field TEXT,
    claim TEXT NOT NULL,
    value TEXT,
    confidence TEXT NOT NULL,
    authority_level TEXT,
    relation_to_profile TEXT NOT NULL,
    conflict_note TEXT,
    resolution TEXT,
    raw_ref JSON,
    created_at TIMESTAMP NOT NULL
)
"""


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA)
    conn.close()
    return db_path


def _insert_rows(db_path: str, rows: list[dict]) -> None:
    conn = duckdb.connect(db_path)
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO unified_evidence
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["evidence_id"],
                row.get("credit_code"),
                row["company_name"],
                row.get("dimension_id"),
                row["source_type"],
                row.get("source_name"),
                row.get("source_path"),
                row.get("source_url"),
                row.get("source_field"),
                row["claim"],
                row.get("value"),
                row.get("confidence", "低"),
                row.get("authority_level", "unknown"),
                row.get("relation_to_profile", "primary"),
                row.get("conflict_note"),
                row.get("resolution"),
                row.get("raw_ref", "{}"),
                row.get("created_at", datetime.now(UTC)),
            ],
        )
    conn.close()


@pytest.fixture
def sample_data(tmp_path: Path) -> str:
    db_path = _make_db(tmp_path)
    rows = [
        {
            "evidence_id": "ev_001",
            "credit_code": "91340521MA2TQP1G4L",
            "company_name": "测试公司",
            "dimension_id": "basic_profile",
            "source_type": "local_json",
            "source_name": "company_profile",
            "source_field": "industry",
            "claim": "行业：制造业",
            "confidence": "中",
            "authority_level": "high",
            "relation_to_profile": "primary",
        },
        {
            "evidence_id": "ev_002",
            "credit_code": "91340521MA2TQP1G4L",
            "company_name": "测试公司",
            "dimension_id": "basic_profile",
            "source_type": "web",
            "source_name": "minimax_search",
            "source_url": "https://example.com/news",
            "source_field": "industry",
            "claim": "行业：电子制造业",
            "confidence": "低",
            "relation_to_profile": "conflict",
            "conflict_note": "Web 显示电子制造，本地为制造业",
        },
        {
            "evidence_id": "ev_003",
            "credit_code": "91340521MA2TQP1G4L",
            "company_name": "测试公司",
            "dimension_id": "supply_chain_procurement",
            "source_type": "web",
            "source_name": "minimax_search",
            "source_url": "https://example.com/supplier",
            "claim": "存在 5 家主要供应商",
            "confidence": "中",
            "relation_to_profile": "supplement",
        },
        {
            "evidence_id": "ev_004",
            "credit_code": "91340521MA2TQP1G4L",
            "company_name": "测试公司",
            "dimension_id": "basic_profile",
            "source_type": "rule",
            "source_name": "dimension_support_rules",
            "claim": "分支机构较多，可能存在多组织管理需求",
            "confidence": "低",
            "relation_to_profile": "inference",
        },
    ]
    _insert_rows(db_path, rows)
    return db_path


class TestEvidenceRepository:
    def test_get_company_evidence(self, sample_data: str) -> None:
        repo = EvidenceRepository(sample_data)
        evs = repo.get_company_evidence("测试公司")
        assert len(evs) == 4
        claims = {e.claim for e in evs}
        assert "行业：制造业" in claims

    def test_get_company_evidence_by_source_type(self, sample_data: str) -> None:
        repo = EvidenceRepository(sample_data)
        evs = repo.get_company_evidence("测试公司", source_type="web")
        assert len(evs) == 2
        assert all(e.source_type == "web" for e in evs)

    def test_get_dimension_evidence(self, sample_data: str) -> None:
        repo = EvidenceRepository(sample_data)
        evs = repo.get_dimension_evidence("测试公司", "basic_profile")
        assert len(evs) == 3
        assert all(e.dimension_id == "basic_profile" for e in evs)

    def test_get_conflicts(self, sample_data: str) -> None:
        repo = EvidenceRepository(sample_data)
        conflicts = repo.get_conflicts("测试公司")
        assert len(conflicts) == 1
        assert conflicts[0].relation_to_profile == "conflict"
        assert "电子制造业" in conflicts[0].claim

    def test_credit_code_filter(self, sample_data: str) -> None:
        repo = EvidenceRepository(sample_data)
        evs = repo.get_company_evidence("测试公司", credit_code="91340521MA2TQP1G4L")
        assert len(evs) == 4

    def test_missing_evidence_summary(self, sample_data: str) -> None:
        # Insert a missing-field marker
        _insert_rows(
            sample_data,
            [
                {
                    "evidence_id": "ev_005",
                    "company_name": "测试公司",
                    "dimension_id": "basic_profile",
                    "source_type": "local_json",
                    "source_name": "company_profile",
                    "claim": "实际控制人：缺失",
                    "confidence": "待补充",
                    "relation_to_profile": "primary",
                    "raw_ref": '{"missing": true}',
                }
            ],
        )
        repo = EvidenceRepository(sample_data)
        summary = repo.get_missing_evidence_summary("测试公司")
        assert "basic_profile" in summary
        assert any("实际控制人" in s for s in summary["basic_profile"])
