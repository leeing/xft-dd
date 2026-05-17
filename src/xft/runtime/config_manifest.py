"""Configuration audit manifest helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from xft.cache.hashing import stable_hash, stable_json_hash
from xft.runtime.artifacts import write_json


class ConfigFileRef(BaseModel):
    """One referenced configuration file and its content hash."""

    path: str
    exists: bool
    sha256: str | None = None
    bytes: int | None = None


class ConfigManifest(BaseModel):
    """Audit manifest for one pipeline run's effective configuration."""

    schema_version: str = "1.0"
    pipeline: str
    run_id: str
    target: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    scenario_id: str | None = None
    scenario_name: str | None = None
    scenario_root: str | None = None
    scenario_resolved_path: str | None = None
    warehouse_db: str | None = None
    mode: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, ConfigFileRef] = Field(default_factory=dict)
    effective_hashes: dict[str, str] = Field(default_factory=dict)


def file_ref(path: str | Path | None) -> ConfigFileRef:
    """Return a file reference with sha256 when the file exists."""
    if path is None:
        return ConfigFileRef(path="", exists=False)
    resolved = Path(path)
    if not resolved.exists() or not resolved.is_file():
        return ConfigFileRef(path=str(resolved), exists=False)
    content = resolved.read_text(encoding="utf-8")
    return ConfigFileRef(
        path=str(resolved),
        exists=True,
        sha256=stable_hash(content),
        bytes=len(content.encode("utf-8")),
    )


def write_config_manifest(path: str | Path, manifest: ConfigManifest) -> Path:
    """Write a config manifest JSON and return its path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, manifest.model_dump(mode="json"))
    return out


def model_hash(model: Any) -> str:
    """Return a stable hash for a Pydantic model or JSON-like mapping."""
    if hasattr(model, "model_dump"):
        value = model.model_dump(mode="json")
    elif isinstance(model, dict):
        value = model
    else:
        value = {"value": str(model)}
    return stable_json_hash(value)
