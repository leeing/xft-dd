"""Configuration loading for the recommender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xft.core.config_loader import load_dimensions_config, load_prompt, read_yaml
from xft.pipeline.recommender.models import ProductsConfig
from xft.pipeline.recommender.scenario import maybe_scenario_path

__all__ = ["load_products_config", "load_dimensions_config", "load_prompt"]


def _read_yaml(path: Path) -> dict[str, Any]:
    return read_yaml(path)


def load_products_config(path: str | Path) -> ProductsConfig:
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_products_config(scenario.products_path)
    config_path = Path(path)
    if config_path.is_dir():
        config_path = config_path / "products.yaml"
    return ProductsConfig.model_validate(_read_yaml(config_path))
