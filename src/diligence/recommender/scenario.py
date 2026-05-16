"""Scenario bundle loading and path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from diligence.recommender.models import ScenarioConfig

DEFAULT_PROMPTS: dict[str, str] = {
    "match_system": "config/recommender/prompts/match_system.md",
    "recommend_system": "config/recommender/prompts/recommend_system.md",
    "web_extract_system": "config/recommender/prompts/extract_evidence_system.md",
}


@dataclass(frozen=True)
class ScenarioBundle:
    """Resolved scenario bundle paths."""

    root: Path
    config: ScenarioConfig

    @property
    def products_path(self) -> str:
        return str(_resolve_path(self.root, self.config.products_config))

    @property
    def dimensions_path(self) -> str:
        return str(_resolve_path(self.root, self.config.dimensions_config))

    @property
    def web_search_path(self) -> str:
        return str(_resolve_path(self.root, self.config.web_search_config))

    @property
    def web_extract_llm_path(self) -> str:
        return str(_resolve_path(self.root, self.config.web_extract_llm_config))

    @property
    def output_dir(self) -> str | None:
        return str(_resolve_path(self.root, self.config.output_dir)) if self.config.output_dir else None

    @property
    def web_cache_root(self) -> str | None:
        return str(_resolve_path(self.root, self.config.web_cache_root)) if self.config.web_cache_root else None

    @property
    def prompt_paths(self) -> dict[str, str]:
        paths = DEFAULT_PROMPTS.copy()
        for key, value in self.config.prompts.items():
            paths[key] = str(_resolve_path(self.root, value))
        return paths


def load_scenario(path: str | Path | None) -> ScenarioBundle | None:
    """Load a scenario bundle from a directory or scenario.yaml path."""
    if path is None:
        return None
    scenario_path = Path(path)
    if scenario_path.is_dir():
        root = scenario_path
        scenario_file = root / "scenario.yaml"
    else:
        scenario_file = scenario_path
        root = scenario_path.parent
    if not scenario_file.exists():
        msg = f"scenario.yaml not found: {scenario_file}"
        raise FileNotFoundError(msg)
    data = yaml.safe_load(scenario_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"scenario.yaml must be a mapping: {scenario_file}"
        raise TypeError(msg)
    return ScenarioBundle(root=root, config=ScenarioConfig.model_validate(data))


def maybe_scenario_path(path: str | Path) -> ScenarioBundle | None:
    """Return a scenario bundle when a path points to one."""
    config_path = Path(path)
    if config_path.is_dir() and (config_path / "scenario.yaml").exists():
        return load_scenario(config_path)
    if config_path.name == "scenario.yaml" and config_path.exists():
        return load_scenario(config_path)
    return None


def _resolve_path(root: Path, value: str | Path | None) -> Path:
    if value is None:
        return root
    path = Path(value)
    return path if path.is_absolute() else root / path
