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
    fetch_enabled: bool = False  # Enable Playwright page fetch to enrich search results
    minimax_queries: list[str]
    metaso_queries: list[str] = Field(default_factory=list)  # 秘塔AI自然语言查询
    summary_prompt: str


class AppConfig(BaseModel):
    """Root application configuration."""

    schema_version: str = "1.0"
    dimension_concurrency: int = Field(default=5, ge=1, le=20)
    query_concurrency_per_dimension: int = Field(default=2, ge=1, le=5)
    search_timeout_seconds: int = 30
    max_results_per_query: int = 10
    runs_dir: str = "runs"
    report_options: ReportOptions = Field(default_factory=ReportOptions)
    batch: BatchConfig = Field(default_factory=BatchConfig)

    # Playwright fetchable domains: list of domain fragments used for URL matching.
    # Any search result whose URL contains one of these fragments will be fetched.
    # Example: ["example.com", "anothersite.cn"]
    fetchable_domains: list[str] = Field(default_factory=list)

    # AI system prompts — configurable to adapt to different industries/scenarios
    summarize_system_prompt: str = (
        "你是中国制造业企业尽调专家，擅长从网络搜索结果中提取和分析企业信息，"
        "对信息的可信度和来源有严格的判断标准。你的输出必须是合法 JSON，不包含任何其他内容。"
    )
    merge_system_prompt: str = (
        "你是一个中国制造业行业顶级专家，对制造业行业有深刻理解，善于综合多维度信息给出精准的企业尽调结论。"
    )

    # Playwright fetch parameters (used when fetch_enabled=true on a dimension)
    playwright_fetch_timeout: int = Field(default=25, ge=5, le=120)
    playwright_fetch_concurrency: int = Field(default=2, ge=1, le=5)
    playwright_headless: bool = True  # headless for production, set false in config for debugging

    merge_prompt: str
    dimensions: list[Dimension]

    @field_validator("dimensions")
    @classmethod
    def sort_by_order(cls, v: list[Dimension]) -> list[Dimension]:
        """Sort dimensions by order field ascending."""
        return sorted(v, key=lambda d: d.order)


def validate_dimension_ids(requested: list[str], available: list[Dimension], *, label: str = "") -> str | None:
    """Validate requested dimension IDs exist in the config.

    Args:
        requested: Dimension IDs from --only or --skip.
        available: All dimensions from the config (enabled + disabled).
        label: Human-readable label for error messages (e.g. "--only", "--skip").

    Returns:
        Error message string if unknown IDs found, None otherwise.
    """
    known_ids = {d.id for d in available}
    unknown = [rid for rid in requested if rid not in known_ids]
    if unknown:
        return f"error: unknown dimension id(s) in {label}: {', '.join(sorted(unknown))}"
    return None


def load_config(config_path: str) -> AppConfig:
    """Load and validate config.yaml. Warns to stderr on schema_version mismatch."""
    raw: dict[str, Any] = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    version = raw.get("schema_version", "")
    if version != SUPPORTED_SCHEMA_VERSION:
        sys.stderr.write(
            f"Warning: schema_version '{version}' != expected '{SUPPORTED_SCHEMA_VERSION}'. Proceeding anyway.\n"
        )
    return AppConfig.model_validate(raw)
