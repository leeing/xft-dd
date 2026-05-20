"""Load business Web search configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xft.core.scenario import maybe_scenario_path
from xft.web.models import WebSearchConfig


def load_web_search_config(path: str | Path) -> WebSearchConfig:
    """Load indicator-level Web search config from a file or scenario bundle."""
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
