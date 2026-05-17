"""DuckDB connection helpers for the enterprise warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


def connect(path: str | Path) -> Any:
    """Open a DuckDB connection, creating the parent directory if needed."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))

