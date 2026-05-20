"""Scenario-agnostic models shared by analysis pipelines."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScenarioConfig(BaseModel):
    """Generic scenario bundle entry config."""

    version: str = "1.0"
    id: str
    name: str
    description: str | None = None
    extends: str | None = None
    web_search_config: str = "web_search.yaml"
    business_modules_config: str | None = None
    prompts: dict[str, str] = Field(default_factory=dict)
    output_dir: str | None = None
    web_cache_root: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    patches: dict[str, Any] = Field(default_factory=dict)
