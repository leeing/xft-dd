"""Configuration models and loader for the due diligence pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

SUPPORTED_SCHEMA_VERSION = "1.0"


class ReportOptions(BaseModel):
    """Report output options."""

    include_sources: bool = True
    include_checklist: bool = True
    max_sources_per_dimension: int = 5


class SourcePolicy(BaseModel):
    """Domain trust classification for confidence scoring."""

    authoritative_domains: list[str] = Field(default_factory=list)
    commercial_sources: list[str] = Field(default_factory=list)


class BatchConfig(BaseModel):
    """Batch mode configuration."""

    company_concurrency: int = Field(default=1, ge=1, le=10)
    continue_on_company_error: bool = True
    skip_existing: bool = True
    batch_runs_dir: str = "batch_runs"


class Dimension(BaseModel):
    """Single due diligence dimension configuration."""

    id: str
    name: str
    order: int
    enabled: bool = True
    required: bool = False
    search_queries: list[str]
    summary_prompt: str


class AppConfig(BaseModel):
    """Root application configuration."""

    schema_version: str = "1.0"
    target: str = ""
    dimension_concurrency: int = Field(default=5, ge=1, le=20)
    query_concurrency_per_dimension: int = Field(default=2, ge=1, le=5)
    search_timeout_seconds: int = 30
    max_results_per_query: int = 10
    model: str
    output_language: str = "zh-CN"
    runs_dir: str = "runs"
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    report_options: ReportOptions = Field(default_factory=ReportOptions)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    merge_prompt: str
    dimensions: list[Dimension]

    @field_validator("dimensions")
    @classmethod
    def sort_by_order(cls, v: list[Dimension]) -> list[Dimension]:
        """Sort dimensions by order field ascending."""
        return sorted(v, key=lambda d: d.order)


def load_config(config_path: str) -> AppConfig:
    """Load and validate config.yaml. Warns to stderr on schema_version mismatch."""
    raw: dict[str, Any] = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    version = raw.get("schema_version", "")
    if version != SUPPORTED_SCHEMA_VERSION:
        sys.stderr.write(
            f"Warning: schema_version '{version}' != expected '{SUPPORTED_SCHEMA_VERSION}'. Proceeding anyway.\n"
        )
    return AppConfig.model_validate(raw)
