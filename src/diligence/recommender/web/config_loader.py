"""Load Web enrichment configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from diligence.recommender.scenario import maybe_scenario_path
from diligence.recommender.web.models import WebExtractLLMConfig, WebSearchConfig


def load_web_search_config(path: str | Path) -> WebSearchConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        config = load_web_search_config(scenario.web_search_path)
        if scenario.web_cache_root:
            config = config.model_copy(update={"cache_root": scenario.web_cache_root})
        return config
    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    return WebSearchConfig.model_validate(data)


def load_web_extract_llm_config(path: str | Path) -> WebExtractLLMConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_web_extract_llm_config(scenario.web_extract_llm_path)
    config_path = Path(path)
    data: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    prompt_file = data.get("prompt_file")
    if isinstance(prompt_file, str) and prompt_file and not Path(prompt_file).is_absolute():
        candidate = config_path.parent / prompt_file
        if candidate.exists():
            data["prompt_file"] = str(candidate)
    return WebExtractLLMConfig.model_validate(data)
