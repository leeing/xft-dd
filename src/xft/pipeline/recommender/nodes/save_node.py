"""Persist recommender outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xft.pipeline.recommender.business_result_renderer import render_business_result_json
from xft.pipeline.recommender.report_renderer import render_report
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.utils.file_io import read_json, write_json, write_jsonl


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
    business_label_path = out_dir / "business_label_result.json"
    llm_calls_path = out_dir / "llm_calls.jsonl"
    llm_metrics_path = out_dir / "llm_metrics.json"
    decision_trace_path = out_dir / "decision_trace.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    write_json(profile_path, state.get("profile", {}))
    llm_events = state.get("llm_call_events", [])
    write_jsonl(llm_calls_path, llm_events)
    write_json(llm_metrics_path, _llm_metrics(llm_events))
    write_json(dimensions_path, [item.model_dump() for item in state["dimension_analysis"]])
    business = state.get("business_recommendation")
    business_label_payload = business.model_dump() if business else {"warning": "business result not generated"}
    write_json(business_label_path, business_label_payload)
    business_payload = render_business_result_json(
        profile=state.get("profile", {}),
        business_result=business,
        config=state.get("business_config"),
    )
    write_json(result_path, business_payload)
    write_json(decision_trace_path, _decision_trace(state, llm_events))
    report_path.write_text(render_report(state), encoding="utf-8")

    status = "failed" if state.get("errors") else "partial" if state.get("needs_web_enrichment") else "success"
    display.done(str(report_path), status=status)
    return {
        "output_dir": str(out_dir),
        "report_path": str(report_path),
        "result_path": str(result_path),
    }


def _decision_trace(state: RecommenderState, llm_events: list[dict[str, Any]]) -> dict[str, Any]:
    web_trace = read_json(Path(state["web_trace_path"])) if state.get("web_trace_path") else {}
    return {
        "company_name": state["company_name"],
        "run_id": state["run_id"],
        "scenario_id": state.get("scenario_id"),
        "web_plan_trace": web_trace,
        "business_rule_trace": _business_rule_trace(state),
        "llm_call_trace": llm_events,
    }


def _business_rule_trace(state: RecommenderState) -> list[dict[str, Any]]:
    business = state.get("business_recommendation")
    config = state.get("business_config")
    if business is None or config is None:
        return []
    config_by_indicator = {
        (module.module_id, label.label_id, indicator.indicator_id): indicator
        for module in config.modules
        for label in module.labels
        for indicator in label.indicators
    }
    rows: list[dict[str, Any]] = []
    for indicator in business.indicator_results:
        cfg = config_by_indicator.get((indicator.module_id, indicator.label_id, indicator.indicator_id))
        rows.append(
            {
                **indicator.model_dump(mode="json"),
                "configured_evaluator": cfg.evaluator if cfg else indicator.evaluator,
                "rule": cfg.rule.model_dump(mode="json") if cfg and cfg.rule else None,
                "prompt": cfg.prompt if cfg else None,
                "evidence_hints": cfg.evidence_hints if cfg else [],
                "decision": (
                    "rule compared source_field/op/value against company_profile"
                    if indicator.evaluator == "rule"
                    else "hybrid combined rule and llm evidence"
                    if indicator.evaluator == "hybrid"
                    else "llm judged against standard using profile and dimension evidence"
                ),
            }
        )
    return rows
