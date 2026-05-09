"""LangGraph state definition for the due diligence pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from diligence.config import AppConfig, Dimension
from diligence.models import RunError


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reducer for fan-in dict accumulation: merge b into a."""
    return {**a, **b}


class DiligenceState(TypedDict):
    """LangGraph state shared across all nodes in the pipeline."""

    # Inputs
    target: str
    config: AppConfig
    run_id: str
    active_dimensions: list[Dimension]
    output_dir: str  # single: runs/{run_id}/ ; batch: batch_runs/{bid}/companies/{idx}-{hash}/

    # Per-branch payload (set via Send API)
    current_dimension: Dimension | None

    # Fan-in accumulators (key = dimension_id)
    search_results_by_dimension: Annotated[dict[str, Any], merge_dicts]
    summaries_by_dimension: Annotated[dict[str, Any], merge_dicts]

    # Error accumulator
    errors: Annotated[list[RunError], operator.add]

    # Outputs
    report: str
    report_path: str
    artifacts_dir: str
