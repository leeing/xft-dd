"""Configuration loading for the recommender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from diligence.recommender.models import AnalysisDimensionsConfig, ProductsConfig
from diligence.recommender.scenario import maybe_scenario_path


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"YAML config must be a mapping: {path}"
        raise TypeError(msg)
    return data


def load_products_config(path: str | Path) -> ProductsConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_products_config(scenario.products_path)
    config_path = Path(path)
    if config_path.is_dir():
        config_path = config_path / "products.yaml"
    return ProductsConfig.model_validate(_read_yaml(config_path))


def load_dimensions_config(path: str | Path) -> AnalysisDimensionsConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_dimensions_config(scenario.dimensions_path)
    config_path = Path(path)
    if not config_path.is_dir():
        return AnalysisDimensionsConfig.model_validate(_read_yaml(config_path))
    legacy_path = config_path / "analysis_dimensions.yaml"
    if legacy_path.exists():
        return AnalysisDimensionsConfig.model_validate(_read_yaml(legacy_path))
    dimensions_dir = config_path / "dimensions"
    dimension_files = sorted(dimensions_dir.glob("*.yaml"))
    if not dimension_files:
        msg = f"config directory has no analysis_dimensions.yaml or dimensions/*.yaml: {config_path}"
        raise FileNotFoundError(msg)
    dimensions: list[dict[str, Any]] = []
    version = "1.0"
    for item_path in dimension_files:
        raw = _read_yaml(item_path)
        if "dimensions" in raw:
            version = str(raw.get("version") or version)
            values = raw.get("dimensions")
            if not isinstance(values, list):
                msg = f"dimensions must be a list: {item_path}"
                raise TypeError(msg)
            dimensions.extend(value for value in values if isinstance(value, dict))
        else:
            dimensions.append(raw)
    return AnalysisDimensionsConfig.model_validate({"version": version, "dimensions": dimensions})


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")
