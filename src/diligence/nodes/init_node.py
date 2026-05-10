"""init_node: generate run_id, determine active dimensions, create output directory."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import structlog

from diligence.config import AppConfig
from diligence.state import DiligenceState

log = structlog.get_logger(__name__)


def make_run_id(target: str) -> str:
    """Generate run_id: YYYYMMDD-HHMMSS-{sha1(target)[:6]}."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    hash6 = hashlib.sha1(target.encode(), usedforsecurity=False).hexdigest()[:6]  # noqa: S324
    return f"{ts}-{hash6}"


def init_node(state: DiligenceState) -> dict[str, object]:
    """Initialise run: generate run_id, filter enabled dimensions, create output dir."""
    config: AppConfig = state["config"]
    target: str = state["target"]

    run_id = make_run_id(target)
    active_dimensions = [d for d in config.dimensions if d.enabled]
    output_dir = state.get("output_dir") or str(Path(config.runs_dir) / run_id)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    log.info("run_initialised", run_id=run_id, dimensions=len(active_dimensions), output_dir=output_dir)

    return {
        "run_id": run_id,
        "active_dimensions": active_dimensions,
        "output_dir": output_dir,
        "search_results_by_dimension": {},
        "summaries_by_dimension": {},
        "errors": [],
        "report": "",
        "report_path": "",
        "artifacts_dir": "",
    }
