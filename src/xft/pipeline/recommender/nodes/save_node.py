"""Persist recommender outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xft.pipeline.recommender.evidence_utils import merge_indicator_evidence
from xft.pipeline.recommender.report_renderer import render_report
from xft.pipeline.recommender.result_renderer import render_result_json
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.utils.file_io import write_json, write_jsonl


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
    display.phase(3, 3, "生成报告")
    out_dir = Path(state["output_root"]) / state["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_path = out_dir / "profile.json"
    business_label_path = out_dir / "label_result.json"
    indicator_evidence_path = out_dir / "indicator_evidence.json"
    web_trace_path = out_dir / "web_trace.json"
    llm_calls_path = out_dir / "llm_calls.jsonl"
    llm_metrics_path = out_dir / "llm_metrics.json"
    decision_trace_path = out_dir / "decision_trace.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    write_json(profile_path, state.get("profile", {}))
    llm_events = state.get("llm_call_events", [])
    write_jsonl(llm_calls_path, llm_events)
    write_json(llm_metrics_path, _llm_metrics(llm_events))
    business = state.get("recommendation")
    write_json(
        indicator_evidence_path,
        merge_indicator_evidence(state.get("evidence", {}), state.get("web_evidence", {})),
    )
    write_json(web_trace_path, {"trace": state.get("web_trace", [])})
    business_label_payload = business.model_dump() if business else {"warning": "business result not generated"}
    write_json(business_label_path, business_label_payload)
    business_payload = render_result_json(
        profile=state.get("profile", {}),
        result=business,
        config=state.get("modules_config"),
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
    return {
        "company_name": state["company_name"],
        "run_id": state["run_id"],
        "scenario_id": state.get("scenario_id"),
        "web_trace": state.get("web_trace", []),
        "rule_trace": _rule_trace(state),
        "llm_call_trace": llm_events,
    }


def _rule_trace(state: RecommenderState) -> list[dict[str, Any]]:
    business = state.get("recommendation")
    config = state.get("modules_config")
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
                "data_sources": [item.model_dump(mode="json") for item in cfg.data_sources] if cfg else [],
                "web_search": cfg.web_search.model_dump(mode="json") if cfg and cfg.web_search else None,
                "prompt": cfg.prompt if cfg else None,
                "evidence_hints": cfg.evidence_hints if cfg else [],
                "decision": (
                    "rule compared configured data_sources or source_field/op/value; "
                    "web_search may add evidence or possible result when configured"
                    if indicator.evaluator == "rule"
                    else "hybrid combined rule, llm, and optional web evidence"
                    if indicator.evaluator == "hybrid"
                    else "llm_web searched public web first and judged against standard"
                    if indicator.evaluator == "llm_web"
                    else "llm judged against standard using profile, indicator evidence, and optional web evidence"
                ),
            }
        )
    return rows
