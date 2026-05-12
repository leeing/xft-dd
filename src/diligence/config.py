"""Configuration models and loader for the due diligence pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

SUPPORTED_SCHEMA_VERSION = "1.0"


class ReportOptions(BaseModel):
    """Report output options."""

    include_sources: bool = True
    include_checklist: bool = True
    max_sources_per_dimension: int = 5


class ExtractField(BaseModel):
    """A field to extract from search result full_text during structured extraction."""

    field_name: str
    description: str = ""
    examples: str | None = None


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
    metaso_mode: Literal["chat", "search"] = "chat"  # chat=AI问答, search=网页搜索(真实URL+rawContent)
    metaso_search_size: int = Field(default=5, ge=1, le=10)  # search模式每次查询返回的网页数 (1-10), 每页消耗6 credits
    summary_prompt: str
    extract_fields: list[ExtractField] | None = None


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

    # Playwright fetch blocklist: search result URLs containing any of these
    # domain fragments will be skipped (e.g. known login-walled sites).
    # When empty, ALL non-metaso URLs are eligible for fetching.
    # Example: ["qixin.com", "tianyancha.com"]
    fetch_blocked_domains: list[str] = Field(default_factory=list)

    # AI system prompts — configurable to adapt to different industries/scenarios
    summarize_system_prompt: str = (
        "你是中国制造业企业尽调专家，擅长从网络搜索结果中提取和分析企业信息，"
        "对信息的可信度和来源有严格的判断标准。你的输出必须是合法 JSON，不包含任何其他内容。"
    )
    merge_system_prompt: str = (
        "你是一个中国制造业行业顶级专家，对制造业行业有深刻理解，善于综合多维度信息给出精准的企业尽调结论。"
    )

    # crawl4ai fetch parameters (used when fetch_enabled=true on a dimension)
    crawl_fetch_timeout: int = Field(default=25, ge=5, le=120)
    crawl_fetch_concurrency: int = Field(default=2, ge=1, le=5)
    max_full_text_chars: int = Field(default=6900, ge=100, le=100000)

    # structured field extraction (used when dimension has extract_fields)
    extract_system_prompt: str = (
        "你是专业的企业信息提取专家。你的任务是严格从给定的网页正文中提取指定字段的值。"
        "\n\n提取规则："
        "\n1. 只提取原文中明确出现的信息，绝对不编造任何值"
        "\n2. 同一字段在不同来源中出现不同值时，全部列出"
        "\n3. 如果某个字段在某个来源中未找到，不要列出该来源"
        "\n4. 对任一来源都未找到的字段，不要出现在输出中"
        "\n\n可信度判断标准："
        "\n- 高：政府网站(gov.cn)、工商登记/企业信息网站(企查查/天眼查/启信宝/爱企查)的公司主页明确列出"
        "\n- 中：商业网站(招聘平台、行业网站、公司官网、制造业采购平台)明确列出"
        "\n- 低：侧面提及、关联信息推断、第三方引用"
    )
    extract_user_template: str = (
        "目标企业：{target}\n\n"
        "需要提取的字段：\n{field_descriptions}\n\n"
        "以下是从 {count} 个不同网页获取的正文内容：\n\n"
        "{item_contents}\n\n"
        "请从以上所有网页正文中提取上述字段的值。不要输出任何文本解释，只输出 JSON。"
    )

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
