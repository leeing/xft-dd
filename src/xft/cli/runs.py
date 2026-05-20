"""CLI for inspecting generated run outputs."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from xft.utils.file_io import read_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xft runs", description="inspect XFT run outputs")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser("inspect", help="inspect recommendation run output directories")
    inspect_parser.add_argument("--runs-dir", default="outputs/recommender/xft")
    inspect_parser.add_argument("--output", help="optional CSV output path")
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _summarize_run(run_dir: Path) -> dict[str, Any]:
    profile = read_json(run_dir / "profile.json")
    result = read_json(run_dir / "result.json")

    # Support both new format (MarketingPoint/Module) and legacy (recommendations)
    raw_points = result.get("MarketingPoint")
    raw_recs = result.get("recommendations")
    if isinstance(raw_points, list) and raw_points:
        items = raw_points
        top_module_name = str(result.get("Module") or "")
        status = "success" if result.get("AcceptanceResult") not in (None, "", "无") else "partial"
    elif isinstance(raw_recs, list) and raw_recs:
        items = raw_recs
        top = items[0] if isinstance(items[0], dict) else {}
        top_module_name = str(top.get("module_name", ""))
        status = "success"
    else:
        items = []
        top_module_name = ""
        status = "failed"

    gaps: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("data_gaps"), list):
            gaps.extend(str(gap) for gap in item["data_gaps"])

    return {
        "run_id": run_dir.name,
        "company_name": profile.get("company_name") or result.get("CompanyName") or result.get("company_name") or "",
        "status": status,
        "top_module_id": "",
        "top_module_name": top_module_name,
        "top_score": str(result.get("AcceptanceResult") or ""),
        "recommendation_count": len(items),
        "profile_completeness": profile.get("profile_completeness", ""),
        "needs_web_enrichment": "",
        "data_gaps": "；".join(sorted(set(gaps))[:12]),
        "output_dir": str(run_dir),
    }


def _write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _print_summary(rows: list[dict[str, Any]]) -> None:
    status_counts = Counter(str(row["status"]) for row in rows)
    top_modules = Counter(str(row["top_module_name"]) for row in rows if row["top_module_name"])
    enrich_count = sum(str(row["needs_web_enrichment"]).lower() == "true" for row in rows)
    completeness: list[float] = []
    for row in rows:
        value = _to_float(row.get("profile_completeness"))
        if value is not None:
            completeness.append(value)
    avg_completeness = sum(completeness) / len(completeness) if completeness else 0.0
    sys.stdout.write(f"runs: {len(rows)}\n")
    sys.stdout.write(f"status: {dict(status_counts)}\n")
    sys.stdout.write(f"needs_web_enrichment: {enrich_count}\n")
    sys.stdout.write(f"avg_profile_completeness: {avg_completeness:.4f}\n")
    sys.stdout.write(f"top_modules: {dict(top_modules.most_common(10))}\n")
    for row in rows[:20]:
        sys.stdout.write(
            f"- {row['company_name']} | {row['top_module_name']} | {row['top_score']} | {row['output_dir']}\n"
        )


def _inspect(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir)
    if not runs_dir.exists():
        sys.stderr.write(f"runs dir not found: {runs_dir}\n")
        return 1
    rows = [
        _summarize_run(path) for path in sorted(runs_dir.iterdir()) if path.is_dir() and (path / "result.json").exists()
    ]
    _print_summary(rows)
    if args.output:
        _write_csv(rows, Path(args.output))
        sys.stdout.write(f"csv: {args.output}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
