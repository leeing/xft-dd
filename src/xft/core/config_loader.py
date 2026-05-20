"""Small configuration loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: str | Path) -> Any:
    """Read a YAML file."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
