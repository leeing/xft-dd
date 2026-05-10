"""LangGraph state definition for the due diligence pipeline."""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any

from typing_extensions import TypedDict

from diligence.config import AppConfig, Dimension
from diligence.models import CostRecord, RunError


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reducer for fan-in dict accumulation: merge b into a."""
    return {**a, **b}


def merge_cost(a: CostRecord, b: CostRecord) -> CostRecord:
    """Reducer for CostRecord: sum all counters across parallel branches."""
    return CostRecord(
        minimax_search_calls=a.minimax_search_calls + b.minimax_search_calls,
        llm_calls=a.llm_calls + b.llm_calls,
        llm_tokens_total=a.llm_tokens_total + b.llm_tokens_total,
        metaso_calls=a.metaso_calls + b.metaso_calls,
        metaso_credits_total=a.metaso_credits_total + b.metaso_credits_total,
    )


def keep_nonempty_str(a: str, b: str) -> str:
    """Reducer for str fields: keep whichever value is non-empty (b wins if both non-empty)."""
    return b if b else a


class DiligenceState(TypedDict):
    """LangGraph state shared across all nodes in the pipeline."""

    # Inputs
    target: str
    config: AppConfig
    run_id: str
    started_at: datetime | None  # set by init_node; None until init completes
    active_dimensions: list[Dimension]
    output_dir: str  # single: runs/{run_id}/ ; batch: batch_runs/{bid}/companies/{idx}-{hash}/

    # Per-branch payload (set via Send API)
    current_dimension: Dimension | None

    # Fan-in accumulators (key = dimension_id)
    search_results_by_dimension: Annotated[dict[str, Any], merge_dicts]
    summaries_by_dimension: Annotated[dict[str, Any], merge_dicts]

    # Error accumulator
    errors: Annotated[list[RunError], operator.add]

    # Cost counters (accumulated across all parallel dimension branches)
    cost: Annotated[CostRecord, merge_cost]

    # Outputs
    report: Annotated[str, keep_nonempty_str]
    report_path: Annotated[str, keep_nonempty_str]
    artifacts_dir: Annotated[str, keep_nonempty_str]
