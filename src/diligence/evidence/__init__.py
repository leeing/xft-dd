"""Unified evidence model, repository, and resolver."""

from diligence.evidence.models import EvidenceRecord
from diligence.evidence.repository import EvidenceRepository
from diligence.evidence.resolver import ResolvedDimensionEvidence, resolve_dimension_evidence

__all__ = [
    "EvidenceRecord",
    "EvidenceRepository",
    "ResolvedDimensionEvidence",
    "resolve_dimension_evidence",
]
