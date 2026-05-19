"""Persist recommender outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xft.pipeline.recommender.business_result_renderer import render_business_result_json
from xft.pipeline.recommender.report_renderer import render_report
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display


def _json_default(value: Any) -> str:
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "\n".join(json.dumps(row, ensure_ascii=False, default=_json_default) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _llm_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    success = sum(1 for item in events if item.get("status") == "success")
    failed = sum(1 for item in events if item.get("status") == "failed")
    elapsed = round(sum(float(item.get("elapsed_seconds") or 0) for item in events), 3)
    by_stage: dict[str, dict[str, Any]] = {}
    for event in events:
        stage = str(event.get("stage") or "unknown")
        bucket = by_stage.setdefault(stage, {"total": 0, "success": 0, "failed": 0, "elapsed_seconds": 0.0})
        bucket["total"] += 1
        if event.get("status") == "success":
            bucket["success"] += 1
        elif event.get("status") == "failed":
            bucket["failed"] += 1
        elapsed = float(bucket["elapsed_seconds"]) + float(event.get("elapsed_seconds") or 0)
        bucket["elapsed_seconds"] = round(elapsed, 3)
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "elapsed_seconds": elapsed,
        "by_stage": by_stage,
    }


async def save_node(state: RecommenderState) -> dict[str, object]:
    display.phase(5, 5, "生成报告")
    out_dir = Path(state["output_root"]) / state["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_path = out_dir / "profile.json"
    dimensions_path = out_dir / "dimension_analysis.json"
    matches_path = out_dir / "match_results.json"
    internal_result_path = out_dir / "internal_result.json"
    business_label_path = out_dir / "business_label_result.json"
    llm_calls_path = out_dir / "llm_calls.jsonl"
    llm_metrics_path = out_dir / "llm_metrics.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    _write_json(profile_path, state.get("profile", {}))
    llm_events = state.get("llm_call_events", [])
    _write_jsonl(llm_calls_path, llm_events)
    _write_json(llm_metrics_path, _llm_metrics(llm_events))
    _write_json(dimensions_path, [item.model_dump() for item in state["dimension_analysis"]])
    _write_json(matches_path, [item.model_dump() for item in state["match_results"]])
    rec = state["recommendation"]
    _write_json(internal_result_path, rec.model_dump() if rec else {"error": "recommendation not generated"})
    business = state.get("business_recommendation")
    business_label_payload = business.model_dump() if business else {"warning": "business result not generated"}
    _write_json(business_label_path, business_label_payload)
    if state.get("business_config") is None:
        business_payload = rec.model_dump() if rec else {"error": "recommendation not generated"}
    else:
        business_payload = render_business_result_json(
            profile=state.get("profile", {}),
            business_result=business,
            config=state.get("business_config"),
        )
    _write_json(result_path, business_payload)
    report_path.write_text(render_report(state), encoding="utf-8")

    status = "failed" if state.get("errors") else "partial" if state.get("needs_web_enrichment") else "success"
    display.done(str(report_path), status=status)
    return {
        "output_dir": str(out_dir),
        "report_path": str(report_path),
        "result_path": str(result_path),
    }
