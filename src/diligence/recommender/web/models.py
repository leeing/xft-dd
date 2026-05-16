"""Models for configurable Web enrichment and cache ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ProviderType = Literal["minimax", "metaso"]
MetasoMode = Literal["search", "chat"]
RecordStatus = Literal["success", "partial", "failed", "skipped"]
EvidenceType = Literal["supplement", "confirmation", "conflict"]


class WebProviderConfig(BaseModel):
    """One configured Web search provider."""

    type: ProviderType
    enabled: bool = True
    mode: MetasoMode | None = None
    search_size: int = Field(default=3, ge=1)
    max_results: int = Field(default=5, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)


class WebExecutionConfig(BaseModel):
    """Execution knobs for Web enrichment."""

    query_concurrency: int = Field(default=3, ge=1)
    provider_concurrency: int = Field(default=2, ge=1)
    max_queries_per_dimension: int = Field(default=3, ge=1)
    max_results_per_query: int = Field(default=5, ge=1)
    dedupe_by_url: bool = True
    fetch_pages: bool = False
    refresh: bool = False
    cache_policy_version: str = "v1-202605"


class WebFetchConfig(BaseModel):
    """crawl4ai fetch settings for Web enrichment."""

    enabled: bool = True
    timeout_seconds: int = Field(default=25, ge=1)
    concurrency: int = Field(default=20, ge=1)
    max_full_text_chars: int = Field(default=12000, ge=100)
    blocked_domains: list[str] = Field(default_factory=list)


class WebSkippedQueryRecord(BaseModel):
    """One skipped Web query with a reason."""

    web_run_id: str
    credit_code: str | None = None
    company_name: str
    dimension_id: str
    query: str
    reason: str
    profile_facts: list[str] = Field(default_factory=list)
    created_at: datetime


class WebSearchConfig(BaseModel):
    """Root Web search config."""

    version: str = "1.0"
    enabled: bool = True
    cache_root: str = "data/web"
    default_providers: list[str] = Field(default_factory=list)
    providers: dict[str, WebProviderConfig]
    execution: WebExecutionConfig = Field(default_factory=WebExecutionConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)

    @model_validator(mode="after")
    def validate_default_providers(self) -> WebSearchConfig:
        missing = [name for name in self.default_providers if name not in self.providers]
        if missing:
            msg = f"unknown default provider(s): {', '.join(missing)}"
            raise ValueError(msg)
        return self


class WebSearchQueryRecord(BaseModel):
    """One executed query record stored as JSONL."""

    query_id: str
    web_run_id: str
    credit_code: str | None = None
    company_name: str
    dimension_id: str
    provider: str
    query: str
    status: RecordStatus
    raw_response_path: str | None = None
    error: str | None = None
    cache_key: str | None = None
    cache_policy_version: str | None = None
    provider_params_hash: str | None = None
    max_results: int | None = None
    created_at: datetime


class WebSearchResultRecord(BaseModel):
    """One normalized Web search result record stored as JSONL."""

    result_id: str
    web_run_id: str
    query_id: str
    credit_code: str | None = None
    company_name: str
    dimension_id: str
    provider: str
    title: str
    url: str | None = None
    snippet: str
    full_text: str = ""
    full_text_preview: str = ""
    content_hash: str | None = None
    page_path: str | None = None
    source: str
    rank: int | None = None
    raw_response_path: str | None = None
    created_at: datetime


class WebPageRecord(BaseModel):
    """Fetched page metadata stored as JSONL and in DuckDB."""

    page_id: str
    web_run_id: str
    result_id: str
    url: str | None = None
    title: str
    content_hash: str | None = None
    page_path: str | None = None
    metadata_path: str | None = None
    text_length: int = 0
    status: RecordStatus
    error: str | None = None
    created_at: datetime


class WebEvidenceRecord(BaseModel):
    """MVP Web evidence derived from normalized search results."""

    evidence_id: str
    web_run_id: str
    result_id: str
    query_id: str
    credit_code: str | None = None
    company_name: str
    dimension_id: str
    provider: str
    claim: str
    evidence_type: EvidenceType = "supplement"
    relation_to_profile: EvidenceType = "supplement"
    confidence: Literal["高", "中", "低", "待补充"] = "低"
    source_url: str | None = None
    source_title: str
    query: str
    source_quote: str | None = None
    json_field: str | None = None
    json_value: str | None = None
    web_value: str | None = None
    conflict_note: str | None = None
    resolution: str | None = None
    extraction_model: str | None = None
    extraction_prompt_version: str | None = None
    extraction_prompt_hash: str | None = None
    extraction_cache_key: str | None = None
    raw_response_path: str | None = None
    created_at: datetime


class WebExtractTaskConfig(BaseModel):
    temperature: float = 0
    timeout_seconds: int = Field(default=90, ge=1)
    max_sources_per_call: int = Field(default=8, ge=1)
    max_chars_per_source: int = Field(default=4000, ge=100)


class WebExtractLLMConfig(BaseModel):
    version: str = "1.0"
    enabled: bool = True
    prompt_file: str = "config/recommender/prompts/extract_evidence_system.md"
    provider: str = "default"
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tasks: dict[str, WebExtractTaskConfig] = Field(
        default_factory=lambda: {"web_evidence_extract": WebExtractTaskConfig()}
    )


class WebRunManifest(BaseModel):
    """Manifest stored with each Web enrichment run."""

    schema_version: str = "1.0"
    web_run_id: str
    company_name: str
    credit_code: str | None = None
    warehouse_db: str
    created_at: datetime
    config: dict[str, str]
    providers: list[str]
    dimensions: list[str]
    status: RecordStatus
    errors: list[str] = Field(default_factory=list)


class ProviderSearchResponse(BaseModel):
    """Provider adapter response."""

    provider: str
    provider_type: ProviderType
    mode: str | None = None
    query: str
    dimension_id: str
    status: RecordStatus
    items: list[Any] = Field(default_factory=list)
    credits: int = 0
    error: str | None = None


class WebRunResult(BaseModel):
    """Public Web enrichment run result."""

    company_name: str
    credit_code: str | None = None
    status: RecordStatus
    web_run_id: str
    output_dir: str
    queries: int
    results: int
    evidence: int
    duckdb_loaded: bool = False
    error: str | None = None


class WebLoadSummary(BaseModel):
    """DuckDB Web cache load summary."""

    runs: int
    queries: int
    results: int
    evidence: int
    table_rows: dict[str, int]
