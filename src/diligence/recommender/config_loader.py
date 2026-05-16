"""Configuration loading for the recommender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from diligence.recommender.models import AnalysisDimensionsConfig, ProductsConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    return data


def load_products_config(path: str | Path) -> ProductsConfig:
    return ProductsConfig.model_validate(_read_yaml(Path(path)))


def load_dimensions_config(path: str | Path) -> AnalysisDimensionsConfig:
    return AnalysisDimensionsConfig.model_validate(_read_yaml(Path(path)))


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")

