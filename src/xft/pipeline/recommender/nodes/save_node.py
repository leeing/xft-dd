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


async def save_node(state: RecommenderState) -> dict[str, object]:
    display.phase(5, 5, "生成报告")
    out_dir = Path(state["output_root"]) / state["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_path = out_dir / "profile.json"
    dimensions_path = out_dir / "dimension_analysis.json"
    matches_path = out_dir / "match_results.json"
    internal_result_path = out_dir / "internal_result.json"
    business_label_path = out_dir / "business_label_result.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    _write_json(profile_path, state.get("profile", {}))
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
