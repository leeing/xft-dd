"""Core data structures for the due xft pipeline.

All models use Pydantic v2 syntax. No BaseModel imports elsewhere —
use these models as the single source of truth for all data shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from xft.core.search_models import DimensionSearchResult, SearchItem, make_item_id

__all__ = [
    "BatchRunMeta",
    "CompanyRunResult",
    "CostRecord",
    "DimensionSearchResult",
    "DimensionSummary",
    "RunError",
    "RunMeta",
    "SearchItem",
    "make_item_id",
]


class DimensionSummary(BaseModel):
    """AI-generated summary for one dimension."""

    dimension_id: str
    dimension_name: str
    status: Literal["success", "partial", "failed", "not_run"]
    summary: str
    confidence: Literal["高", "中", "低", "待核实"]
    uncertain_facts: list[str]
    evidence_item_ids: list[str]
    error: str | None = None


class CostRecord(BaseModel):
    """API usage counters for one run."""

    minimax_search_calls: int = 0  # MiniMax Search POST 成功次数
    llm_calls: int = 0  # LLM completions.create 调用次数（含 JSON retry）
    llm_tokens_total: int = 0  # LLM total_tokens 累计
    metaso_calls: int = 0  # Metaso 查询成功次数
    metaso_failed_calls: int = 0  # Metaso 查询失败次数
    metaso_credits_total: int = 0  # Metaso credits 累计


class RunError(BaseModel):
    """Error record for a specific pipeline stage."""

    dimension_id: str | None = None
    stage: Literal[
        "config",
        "input",
        "init",
        "search",
        "summarize",
        "collect",
        "merge",
        "save",
        "batch",
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
    cost: CostRecord = Field(default_factory=CostRecord)


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
