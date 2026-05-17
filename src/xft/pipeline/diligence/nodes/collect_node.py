"""collect_node: fan-in gate -- verify all dimensions produced a summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog

from xft.pipeline.diligence.config import Dimension
from xft.pipeline.diligence.models import DimensionSummary, RunError
from xft.pipeline.diligence.state import DiligenceState

log = structlog.get_logger(__name__)

_COLLECT_STAGE: Literal["collect"] = "collect"


def collect_node(state: DiligenceState) -> dict[str, object]:
    """Verify completeness and flag required-dimension failures."""
    active: list[Dimension] = state["active_dimensions"]
    summaries: dict[str, DimensionSummary] = state["summaries_by_dimension"]

    active_ids = {d.id for d in active}
    missing = active_ids - set(summaries.keys())
    errors: list[RunError] = [
        RunError(
            dimension_id=dim_id,
            stage=_COLLECT_STAGE,
            message=f"dimension {dim_id} missing from summaries",
            timestamp=datetime.now(UTC),
        )
        for dim_id in missing
    ]

    for dim in active:
        if dim.required:
            s: DimensionSummary | None = summaries.get(dim.id)
            if s is None or s.status in ("failed", "partial"):
                errors.append(
                    RunError(
                        dimension_id=dim.id,
                        stage=_COLLECT_STAGE,
                        message=f"核心维度 [{dim.name}] {s.status if s else 'missing'}, required dimension failure",
                        timestamp=datetime.now(UTC),
                    )
                )
                log.warning("required_dimension_failed", dimension=dim.id, status=s.status if s else "missing")

    return {"errors": errors}
