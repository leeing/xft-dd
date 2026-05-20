"""Load business-facing recommendation configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xft.core.config_loader import read_yaml
from xft.core.scenario import maybe_scenario_path
from xft.pipeline.recommender.models import RecommendationConfig


def load_recommendation_config(path: str | Path | None) -> RecommendationConfig | None:
    """Load optional business recommendation config."""
    if path is None:
        return None
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_recommendation_config(scenario.modules_path)
    config_path = Path(path)
    if config_path.is_dir():
        config_path = config_path / "modules.yaml"
    if not config_path.exists():
        return None
    return RecommendationConfig.model_validate(_load_modules_config_data(config_path))


def _load_modules_config_data(config_path: Path) -> dict[str, Any]:
    data = read_yaml(config_path)
    if not isinstance(data, dict):
        msg = f"modules.yaml must be a mapping: {config_path}"
        raise TypeError(msg)
    modules = list(_as_list(data.get("modules")))
    modules_dir = data.get("modules_dir")
    if isinstance(modules_dir, str) and modules_dir.strip():
        module_dir_path = Path(modules_dir)
        if not module_dir_path.is_absolute():
            module_dir_path = config_path.parent / module_dir_path
        modules.extend(_load_module_dir(module_dir_path))
    merged = dict(data)
    merged["modules"] = modules
    return merged


def _load_module_dir(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        msg = f"business modules directory not found: {path}"
        raise FileNotFoundError(msg)
    if not path.is_dir():
        msg = f"business modules directory is not a directory: {path}"
        raise NotADirectoryError(msg)
    modules: list[dict[str, Any]] = []
    for module_path in sorted(path.glob("*.yaml")):
        raw = read_yaml(module_path)
        if not isinstance(raw, dict):
            msg = f"business module file must be a mapping: {module_path}"
            raise TypeError(msg)
        modules.extend(_as_list(raw.get("modules") if "modules" in raw else raw))
    return modules


def _as_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            msg = "business modules list must contain mappings"
            raise TypeError(msg)
        return value
    if isinstance(value, dict):
        return [value]
    msg = "business modules must be a mapping or list of mappings"
    raise TypeError(msg)
