"""Persist recommender outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diligence.recommender.progress import display
from diligence.recommender.report_renderer import render_report
from diligence.recommender.state import RecommenderState


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
    result_path = out_dir / "result.json"
    report_path = out_dir / "report.md"

    _write_json(profile_path, state.get("profile", {}))
    _write_json(dimensions_path, [item.model_dump() for item in state["dimension_analysis"]])
    _write_json(matches_path, [item.model_dump() for item in state["match_results"]])
    rec = state["recommendation"]
    _write_json(result_path, rec.model_dump() if rec else {"error": "recommendation not generated"})
    report_path.write_text(render_report(state), encoding="utf-8")

    status = "failed" if state.get("errors") else "partial" if state.get("needs_web_enrichment") else "success"
    display.done(str(report_path), status=status)
    return {
        "output_dir": str(out_dir),
        "report_path": str(report_path),
        "result_path": str(result_path),
    }
