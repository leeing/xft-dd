"""Models for business indicator-level Web search."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ProviderType = Literal["minimax", "metaso"]
MetasoMode = Literal["search", "chat"]
RecordStatus = Literal["success", "partial", "failed", "skipped"]


class WebProviderConfig(BaseModel):
    """One configured Web search provider."""

    type: ProviderType
    enabled: bool = True
    mode: MetasoMode | None = None
    search_size: int = Field(default=3, ge=1)
    max_results: int = Field(default=5, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)


class WebExecutionConfig(BaseModel):
    """Execution knobs for business Web search."""

    max_results_per_query: int = Field(default=5, ge=1)


class WebSearchConfig(BaseModel):
    """Root Web search config."""

    version: str = "1.0"
    enabled: bool = True
    cache_root: str = "data/web_business"
    default_providers: list[str] = Field(default_factory=list)
    providers: dict[str, WebProviderConfig]
    execution: WebExecutionConfig = Field(default_factory=WebExecutionConfig)

    @model_validator(mode="after")
    def validate_default_providers(self) -> WebSearchConfig:
        missing = [name for name in self.default_providers if name not in self.providers]
        if missing:
            msg = f"unknown default provider(s): {', '.join(missing)}"
            raise ValueError(msg)
        return self


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
