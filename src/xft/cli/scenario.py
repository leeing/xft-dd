"""CLI for scenario inspection and validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xft.core.config_loader import load_dimensions_config
from xft.core.scenario import load_scenario
from xft.evidence.policy import load_evidence_policy
from xft.pipeline.recommender.business_config_loader import load_business_recommendation_config
from xft.web.config_loader import load_web_extract_llm_config, load_web_search_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="scenario bundle commands")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="write or print resolved scenario configuration")
    inspect.add_argument("scenario")
    inspect.add_argument("--output", help="write resolved JSON to this path")
    inspect.add_argument("--print", action="store_true", dest="print_json", help="print resolved JSON to stdout")
    validate = sub.add_parser("validate", help="validate scenario bundle and referenced configs")
    validate.add_argument("scenario")
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
        dimensions = load_dimensions_config(args.scenario)
        web_search = load_web_search_config(args.scenario)
        web_extract = load_web_extract_llm_config(args.scenario)
        evidence = load_evidence_policy(args.scenario)
        business = load_business_recommendation_config(args.scenario)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"invalid scenario: {exc}\n")
        return 1
    payload = {
        "scenario_id": scenario.config.id,
        "scenario_name": scenario.config.name,
        "root": str(Path(args.scenario)),
        "dimensions": len(dimensions.dimensions),
        "web_enabled": web_search.enabled,
        "web_extract_enabled": web_extract.enabled,
        "evidence_policy_version": evidence.version,
        "business_modules": len(business.modules) if business else 0,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "validate":
        return _validate(args)
    return 2
