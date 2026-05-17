"""save_node: write all pipeline artifacts to the output directory."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import structlog

from xft.pipeline.diligence.config import AppConfig
from xft.pipeline.diligence.models import CostRecord, DimensionSearchResult, DimensionSummary, RunMeta
from xft.pipeline.diligence.state import DiligenceState

log = structlog.get_logger(__name__)


def _default_serializer(obj: object) -> object:
    if isinstance(obj, datetime):
        return obj.isoformat()
    msg = f"Not serializable: {type(obj)}"
    raise TypeError(msg)


def save_node(state: DiligenceState) -> dict[str, object]:
    """Persist final_report.md, raw_search_results.json, dimension_summaries.json, run_meta.json."""
    output_dir = Path(state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    config: AppConfig = state["config"]
    target: str = state["target"]
    run_id: str = state["run_id"]
    report: str = state["report"]
    summaries: dict[str, DimensionSummary] = state["summaries_by_dimension"]
    search_results: dict[str, DimensionSearchResult] = state["search_results_by_dimension"]
    active_dims = state["active_dimensions"]
    cost: CostRecord = state["cost"]
    started_at_raw = state["started_at"]
    if started_at_raw is None:
        log.warning("started_at_missing", note="init_node may have failed; using save time as fallback")
        started_at_raw = datetime.now(UTC)
    started_at: datetime = started_at_raw

    active_dim_ids = {d.id for d in active_dims}
    failed_dims = [s.dimension_id for s in summaries.values() if s.status in ("failed", "partial")]
    # Active dimensions missing from summaries also count as failed
    missing_active = active_dim_ids - set(summaries.keys())
    failed_dims = sorted(set(failed_dims) | missing_active)
    required_failed = any(d.required and d.id in failed_dims for d in active_dims)
    run_status: Literal["success", "partial", "failed"]
    run_status = "success" if not failed_dims and report else ("partial" if report else "failed")

    report_path = output_dir / "final_report.md"
    report_path.write_text(report, encoding="utf-8")

    raw = {dim_id: dsr.model_dump(mode="json") for dim_id, dsr in search_results.items()}
    (output_dir / "raw_search_results.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, default=_default_serializer),
        encoding="utf-8",
    )

    sums = {dim_id: s.model_dump(mode="json") for dim_id, s in summaries.items()}
    (output_dir / "dimension_summaries.json").write_text(
        json.dumps(sums, ensure_ascii=False, indent=2, default=_default_serializer),
        encoding="utf-8",
    )

    finished_at = datetime.now(UTC)
    meta = RunMeta(
        run_id=run_id,
        target=target,
        started_at=started_at,
        finished_at=finished_at,
        status=run_status,
        required_failed=required_failed,
        failed_dimensions=failed_dims,
        config_path=state.get("config_path") or config.runs_dir,
        active_dimensions=[d.id for d in active_dims],
        cost=cost,
    )
    (output_dir / "run_meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")

    elapsed = (finished_at - started_at).total_seconds()
    log.info("artifacts_saved", output_dir=str(output_dir), status=run_status, elapsed_seconds=elapsed)
    sys.stderr.write(f"\nArtifacts saved: {output_dir}/\n")
    sys.stderr.write(f"⏱️  总耗时：{elapsed:.1f} 秒\n")
    sys.stderr.write("💰 本次调用成本：\n")
    sys.stderr.write(f"   MiniMax Search: {cost.minimax_search_calls} 次\n")
    sys.stderr.write(f"   LLM 推理: {cost.llm_calls} 次，tokens: {cost.llm_tokens_total:,}\n")
    if cost.metaso_calls > 0 or cost.metaso_failed_calls > 0:
        sys.stderr.write(
            f"   Metaso: {cost.metaso_calls} 次成功，"
            f"{cost.metaso_failed_calls} 次失败，credits: {cost.metaso_credits_total}\n"
        )

    return {"report_path": str(report_path), "artifacts_dir": str(output_dir)}
