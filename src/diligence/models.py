"""Core data structures for the due diligence pipeline.

All models use Pydantic v2 syntax. No BaseModel imports elsewhere —
use these models as the single source of truth for all data shapes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def make_item_id(*, url: str | None, title: str, snippet: str) -> str:
    """Stable 12-char sha1 ID: prefer URL, fallback to title+snippet concatenation."""
    key = url if url else (title + snippet)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


class SearchItem(BaseModel):
    """Single search result from mmx search."""

    id: str
    title: str
    url: str | None = None
    snippet: str
    query: str
    dimension_id: str
    rank: int | None = None
    fetched_at: datetime


class DimensionSearchResult(BaseModel):
    """Aggregated search results for one dimension."""

    dimension_id: str
    dimension_name: str
    status: Literal["success", "partial", "failed"]
    items: list[SearchItem]
    error: str | None = None


class DimensionSummary(BaseModel):
    """AI-generated summary for one dimension."""

    dimension_id: str
    dimension_name: str
    status: Literal["success", "partial", "failed"]
    summary: str
    confidence: Literal["高", "中", "低", "待核实"]
    uncertain_facts: list[str]
    evidence_item_ids: list[str]
    error: str | None = None


class RunError(BaseModel):
    """Error record for a specific pipeline stage."""

    dimension_id: str | None = None
    stage: Literal[
        "config", "input", "init", "search", "summarize",
        "collect", "merge", "save", "batch",
    ]
    message: str
    timestamp: datetime


class RunMeta(BaseModel):
    """Persisted metadata for a single company run."""

    run_id: str
    target: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["success", "partial", "failed"]
    required_failed: bool = False
    failed_dimensions: list[str] = Field(default_factory=list)
    config_path: str
    active_dimensions: list[str]


class CompanyRunResult(BaseModel):
    """Result of processing one company (used by batch layer)."""

    index: int
    target: str
    run_id: str | None = None
    status: Literal["success", "partial", "failed", "skipped"]
    report_path: str | None = None
    artifacts_dir: str | None = None
    required_failed: bool = False
    failed_dimensions: list[str] = Field(default_factory=list)
    error: str | None = None


class BatchRunMeta(BaseModel):
    """Persisted metadata for a batch run."""

    batch_id: str
    input_file: str | None = None
    index_target_map: dict[int, str]
    total: int
    success: int
    partial: int
    failed: int
    skipped: int
    started_at: datetime
    finished_at: datetime | None = None
    config_path: str
