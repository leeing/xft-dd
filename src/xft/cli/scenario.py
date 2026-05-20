"""CLI for scenario inspection and validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xft.core.scenario import load_scenario
from xft.pipeline.recommender.config_loader import load_recommendation_config
from xft.pipeline.recommender.scenario_audit import audit_recommendation_config, render_audit_text
from xft.web.config_loader import load_web_search_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="scenario bundle commands")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="write or print resolved scenario configuration")
    inspect.add_argument("scenario")
    inspect.add_argument("--output", help="write resolved JSON to this path")
    inspect.add_argument("--print", action="store_true", dest="print_json", help="print resolved JSON to stdout")
    validate = sub.add_parser("validate", help="validate scenario bundle and referenced configs")
    validate.add_argument("scenario")
    audit = sub.add_parser("audit", help="audit recommendation module configuration for tuning")
    audit.add_argument("scenario")
    audit.add_argument("--json", action="store_true", dest="json_output", help="print machine-readable JSON")
    return parser


def _inspect(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    if scenario is None:
        sys.stderr.write(f"error: scenario not found: {args.scenario}\n")
        return 2
    output = args.output or None
    path = scenario.write_resolved_config(output)
    if args.print_json:
        sys.stdout.write(path.read_text(encoding="utf-8"))
        if not path.read_text(encoding="utf-8").endswith("\n"):
            sys.stdout.write("\n")
    else:
        sys.stdout.write(f"scenario_resolved: {path}\n")
    return 0


def _validate(args: argparse.Namespace) -> int:
    try:
        scenario = load_scenario(args.scenario)
        if scenario is None:
            sys.stderr.write(f"error: scenario not found: {args.scenario}\n")
            return 2
        web_search = load_web_search_config(args.scenario)
        business = load_recommendation_config(args.scenario)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"invalid scenario: {exc}\n")
        return 1
    payload = {
        "scenario_id": scenario.config.id,
        "scenario_name": scenario.config.name,
        "root": str(Path(args.scenario)),
        "web_enabled": web_search.enabled,
        "modules": len(business.modules) if business else 0,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


def _audit(args: argparse.Namespace) -> int:
    try:
        business = load_recommendation_config(args.scenario)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"invalid scenario: {exc}\n")
        return 1
    payload = audit_recommendation_config(business)
    if args.json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_audit_text(payload))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "audit":
        return _audit(args)
    return 2
