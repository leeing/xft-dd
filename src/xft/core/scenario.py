"""Scenario bundle loading and path resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from xft.core.models import ScenarioConfig


@dataclass(frozen=True)
class ScenarioBundle:
    """Resolved scenario bundle paths."""

    root: Path
    config: ScenarioConfig

    @property
    def web_search_path(self) -> str:
        return str(_resolve_path(self.root, self.config.web_search_config))

    @property
    def business_modules_path(self) -> str | None:
        if not self.config.business_modules_config:
            return None
        return str(_resolve_path(self.root, self.config.business_modules_config))

    @property
    def output_dir(self) -> str | None:
        return str(_resolve_path(self.root, self.config.output_dir)) if self.config.output_dir else None

    @property
    def web_cache_root(self) -> str | None:
        return str(_resolve_path(self.root, self.config.web_cache_root)) if self.config.web_cache_root else None

    @property
    def prompt_paths(self) -> dict[str, str]:
        paths: dict[str, str] = {}
        for key, value in self.config.prompts.items():
            paths[key] = str(_resolve_path(self.root, value))
        return paths

    def resolved_payload(self) -> dict[str, Any]:
        """Return the fully resolved scenario config for audit/debugging."""
        payload = self.config.model_dump(mode="json", exclude_none=True)
        payload["root"] = str(self.root)
        payload["web_search_path"] = self.web_search_path
        payload["business_modules_path"] = self.business_modules_path
        payload["prompt_paths"] = self.prompt_paths
        return payload

    def write_resolved_config(self, path: str | Path | None = None) -> Path:
        """Write scenario_resolved.json for auditability and return its path."""
        return self.write_resolved_config_payload(self.resolved_payload(), path=path)

    def write_resolved_config_payload(self, payload: dict[str, Any], path: str | Path | None = None) -> Path:
        """Write a resolved scenario payload for auditability and return its path."""
        out = Path(path) if path is not None else self.root / "scenario_resolved.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out


def load_scenario(path: str | Path | None) -> ScenarioBundle | None:
    """Load a scenario bundle from a directory or scenario.yaml path."""
    if path is None:
        return None
    scenario_file, root = _scenario_file_and_root(path)
    if not scenario_file.exists():
        msg = f"scenario.yaml not found: {scenario_file}"
        raise FileNotFoundError(msg)
    data = _load_scenario_data(scenario_file, stack=[])
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


def _scenario_file_and_root(path: str | Path) -> tuple[Path, Path]:
    scenario_path = Path(path)
    if scenario_path.is_dir():
        root = scenario_path
        scenario_file = root / "scenario.yaml"
    else:
        scenario_file = scenario_path
        root = scenario_path.parent
    return scenario_file, root


def _load_scenario_data(scenario_file: Path, *, stack: list[Path]) -> dict[str, Any]:
    scenario_file = scenario_file.resolve()
    if scenario_file in stack:
        cycle = " -> ".join(str(item) for item in [*stack, scenario_file])
        msg = f"scenario extends cycle detected: {cycle}"
        raise ValueError(msg)
    raw = yaml.safe_load(scenario_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"scenario.yaml must be a mapping: {scenario_file}"
        raise TypeError(msg)
    root = scenario_file.parent
    parent: dict[str, Any] = {}
    extends = raw.get("extends")
    if isinstance(extends, str) and extends.strip():
        parent_file, _ = _scenario_file_and_root(_resolve_path(root, extends))
        parent = _load_scenario_data(parent_file, stack=[*stack, scenario_file])
    explicit = {key: value for key, value in raw.items() if key not in {"extends", "overrides", "patches"}}
    raw_overrides = raw.get("overrides")
    overrides: dict[str, Any] = raw_overrides if isinstance(raw_overrides, dict) else {}
    raw_patches = raw.get("patches")
    patches: dict[str, Any] = raw_patches if isinstance(raw_patches, dict) else {}
    child = _deep_merge(explicit, overrides)
    child = _resolve_config_paths(root, child)
    merged = _deep_merge(parent, child)
    merged["extends"] = str(_resolve_path(root, extends)) if isinstance(extends, str) and extends.strip() else None
    merged["overrides"] = overrides
    parent_patches = parent.get("patches", {})
    merged["patches"] = _deep_merge(parent_patches if isinstance(parent_patches, dict) else {}, patches)
    return merged


def _resolve_config_paths(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(data)
    for field in (
        "web_search_config",
        "business_modules_config",
    ):
        value = resolved.get(field)
        if isinstance(value, str) and value:
            resolved[field] = str(_resolve_path(root, value))
    for field in ("output_dir", "web_cache_root"):
        value = resolved.get(field)
        if isinstance(value, str) and value:
            resolved[field] = str(_resolve_path(root, value))
    prompts = resolved.get("prompts")
    if isinstance(prompts, dict):
        resolved["prompts"] = {
            str(key): str(_resolve_path(root, value)) if isinstance(value, str) and value else value
            for key, value in prompts.items()
        }
    return resolved


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
