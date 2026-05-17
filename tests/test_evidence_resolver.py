"""Tests for evidence.resolver conflict resolution and deduplication."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from xft.evidence.models import (
    AuthorityLevel,
    EvidenceConfidence,
    EvidenceRecord,
    EvidenceRelation,
    EvidenceSourceType,
)
from xft.evidence.policy import EvidencePolicy
from xft.evidence.resolver import (
    _deduplicate,
    resolve_dimension_evidence,
)


def _ev(  # noqa: PLR0913
    *,
    evidence_id: str = "ev_1",
    claim: str = "test",
    source_type: str = "local_json",
    source_field: str | None = None,
    source_url: str | None = None,
    relation_to_profile: str = "primary",
    confidence: str = "中",
    authority_level: str = "high",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        company_name="测试公司",
        dimension_id="basic_profile",
        source_type=cast(EvidenceSourceType, source_type),
        source_name=source_type,
        source_field=source_field,
        source_url=source_url,
        claim=claim,
        confidence=cast(EvidenceConfidence, confidence),
        authority_level=cast(AuthorityLevel, authority_level),
        relation_to_profile=cast(EvidenceRelation, relation_to_profile),
        created_at=datetime.now(UTC),
    )


class TestResolveDimensionEvidence:
    def test_empty_returns_missing(self) -> None:
        resolved = resolve_dimension_evidence([], missing_fields=["实际控制人"])
        assert resolved.dimension_id == ""
        assert resolved.missing_evidence == ["实际控制人"]
        assert resolved.quality_score == 0.0

    def test_local_json_becomes_primary(self) -> None:
        evs = [
            _ev(source_type="local_json", claim="行业：制造业", source_field="industry"),
        ]
        resolved = resolve_dimension_evidence(evs)
        assert len(resolved.primary_evidence) == 1
        assert resolved.primary_evidence[0].claim == "行业：制造业"
        assert resolved.quality_score == 15.0

    def test_web_supplement_without_local(self) -> None:
        evs = [
            _ev(source_type="web", claim="官网显示主营电子产品", source_url="https://example.com"),
        ]
        resolved = resolve_dimension_evidence(evs)
        # No local for this field, web should be promoted to primary
        assert len(resolved.primary_evidence) == 1
        assert resolved.primary_evidence[0].relation_to_profile == "primary"

    def test_web_conflict_with_local_same_field(self) -> None:
        evs = [
            _ev(
                source_type="local_json",
                claim="行业：制造业",
                source_field="industry",
                relation_to_profile="primary",
            ),
            _ev(
                source_type="web",
                claim="行业：电子制造业",
                source_field="industry",
                source_url="https://example.com",
                relation_to_profile="conflict",
            ),
        ]
        resolved = resolve_dimension_evidence(evs)
        # Local stays primary, web conflict goes to conflict list
        assert len(resolved.primary_evidence) == 1
        assert resolved.primary_evidence[0].claim == "行业：制造业"
        assert len(resolved.conflict_evidence) == 1
        assert resolved.conflict_evidence[0].resolution == "use_local"
        # Quality score: 15 (primary) - 10 (conflict) = 5
        assert resolved.quality_score == 5.0

    def test_web_confirmation_kept(self) -> None:
        evs = [
            _ev(source_type="local_json", claim="本地数据"),
            _ev(
                source_type="web",
                claim="员工规模约 500 人",
                source_url="https://example.com",
                relation_to_profile="confirmation",
            ),
        ]
        resolved = resolve_dimension_evidence(evs)
        assert len(resolved.confirmation_evidence) == 1
        assert resolved.confirmation_evidence[0].claim == "员工规模约 500 人"

    def test_deduplication_by_claim_source_field(self) -> None:
        evs = [
            _ev(evidence_id="a", claim="重复claim", source_type="local_json"),
            _ev(evidence_id="b", claim="重复claim", source_type="local_json"),
        ]
        resolved = resolve_dimension_evidence(evs)
        assert len(resolved.primary_evidence) == 1

    def test_different_source_types_not_deduped(self) -> None:
        evs = [
            _ev(evidence_id="a", claim="相同claim", source_type="local_json"),
            _ev(evidence_id="b", claim="相同claim", source_type="web"),
        ]
        resolved = resolve_dimension_evidence(evs)
        # Same claim from different sources: both kept (different keys)
        total = len(resolved.primary_evidence) + len(resolved.supplement_evidence)
        assert total == 2

    def test_source_authority_boost_high(self) -> None:
        evs = [
            _ev(
                source_type="web",
                claim="政府公告",
                source_url="https://www.gsxt.gov.cn/xxx",
                confidence="低",
            ),
        ]
        resolved = resolve_dimension_evidence(evs)
        # High authority source should boost confidence from 低 to 中
        assert resolved.primary_evidence[0].confidence == "中"

    def test_source_authority_boost_gov_cn(self) -> None:
        evs = [
            _ev(
                source_type="web",
                claim="行政许可信息",
                source_url="https://beijing.gov.cn/notice",
                confidence="低",
            ),
        ]
        resolved = resolve_dimension_evidence(evs)
        assert resolved.primary_evidence[0].confidence == "中"

    def test_rule_inference_separated(self) -> None:
        evs = [
            _ev(source_type="rule", claim="推断存在采购需求", relation_to_profile="inference"),
        ]
        resolved = resolve_dimension_evidence(evs)
        assert len(resolved.inference_evidence) == 1
        assert len(resolved.primary_evidence) == 0

    def test_quality_score_computation(self) -> None:
        evs = [
            _ev(source_type="local_json", claim="A"),
            _ev(source_type="local_json", claim="B"),
            _ev(source_type="web", claim="C", relation_to_profile="confirmation"),
            _ev(source_type="web", claim="D", relation_to_profile="supplement"),
            _ev(source_type="rule", claim="E", relation_to_profile="inference"),
        ]
        resolved = resolve_dimension_evidence(evs)
        # 2*15 (primary) + 1*10 (confirmation) + 1*5 (supplement) + 1*3 (inference) = 48
        assert resolved.quality_score == 48.0

    def test_quality_score_uses_policy_weights(self) -> None:
        evs = [
            _ev(source_type="local_json", claim="A"),
            _ev(source_type="web", claim="B", relation_to_profile="confirmation"),
        ]
        policy = EvidencePolicy.model_validate(
            {
                "resolver": {
                    "quality_score": {
                        "primary": 20,
                        "confirmation": 7,
                        "supplement": 0,
                        "inference": 0,
                        "conflict_penalty": 0,
                    }
                }
            }
        )

        resolved = resolve_dimension_evidence(evs, policy=policy)

        assert resolved.quality_score == 27.0

    def test_missing_fields_preserved(self) -> None:
        evs = [
            _ev(source_type="local_json", claim="有数据"),
        ]
        resolved = resolve_dimension_evidence(evs, missing_fields=["缺字段1", "缺字段2"])
        assert resolved.missing_evidence == ["缺字段1", "缺字段2"]


class TestDeduplicate:
    def test_removes_exact_duplicates(self) -> None:
        evs = [
            _ev(evidence_id="a", claim="same", source_type="local_json", source_field="f1"),
            _ev(evidence_id="b", claim="same", source_type="local_json", source_field="f1"),
        ]
        result = _deduplicate(evs)
        assert len(result) == 1

    def test_keeps_different_fields(self) -> None:
        evs = [
            _ev(evidence_id="a", claim="same", source_type="local_json", source_field="f1"),
            _ev(evidence_id="b", claim="same", source_type="local_json", source_field="f2"),
        ]
        result = _deduplicate(evs)
        assert len(result) == 2

    def test_keeps_different_urls(self) -> None:
        evs = [
            _ev(evidence_id="a", claim="same", source_type="web", source_url="http://a.com"),
            _ev(evidence_id="b", claim="same", source_type="web", source_url="http://b.com"),
        ]
        result = _deduplicate(evs)
        assert len(result) == 2
