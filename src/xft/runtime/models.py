"""Common request/result models for scenario pipelines."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PipelineId = Literal["recommender", "diligence"]
PipelineStatus = Literal["success", "partial", "failed", "skipped"]


class PipelineRunRequest(BaseModel):
    """Common input envelope for running one target through a pipeline."""

    pipeline: PipelineId
    target: str
    warehouse_db: str = "cache/company_warehouse.duckdb"
    scenario_path: str | None = None
    config_path: str | None = None
    output_dir: str | None = None
    run_id: str | None = None
    use_llm: bool = True
    use_web: bool = False
    use_web_evidence: bool = False
    refresh_web: bool = False
    only_dimensions: list[str] | None = None
    skip_dimensions: list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class PipelineRunResult(BaseModel):
    """Common output envelope returned by all scenario pipelines."""

    pipeline: PipelineId
    target: str
    status: PipelineStatus
    run_id: str
    output_dir: str
    result_path: str | None = None
    report_path: str | None = None
    artifacts_dir: str | None = None
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

