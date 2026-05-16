"""Load Web enrichment configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from diligence.recommender.web.models import WebExtractLLMConfig, WebSearchConfig


def load_web_search_config(path: str | Path) -> WebSearchConfig:
    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    return WebSearchConfig.model_validate(data)


def load_web_extract_llm_config(path: str | Path) -> WebExtractLLMConfig:
    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    return WebExtractLLMConfig.model_validate(data)
