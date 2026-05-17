"""Unified evidence model, repository, and resolver."""

from xft.evidence.models import EvidenceRecord
from xft.evidence.repository import EvidenceRepository
from xft.evidence.resolver import ResolvedDimensionEvidence, resolve_dimension_evidence

__all__ = [
    "EvidenceRecord",
    "EvidenceRepository",
    "ResolvedDimensionEvidence",
    "resolve_dimension_evidence",
]
