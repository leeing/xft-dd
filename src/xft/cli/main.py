"""Top-level `xft` command."""

from __future__ import annotations

import sys
from collections.abc import Callable

from xft.cli import cache, calibrate, diligence, recommend, runs, scenario, warehouse, web

Command = Callable[[list[str] | None], int]

COMMANDS: dict[str, Command] = {
    "recommend": recommend.main,
    "diligence": diligence.main,
    "calibrate": calibrate.main,
    "web": web.main,
    "warehouse": warehouse.main,
    "scenario": scenario.main,
    "runs": runs.main,
    "cache": cache.main,
}


def _print_help() -> None:
    sys.stdout.write(
        "\n".join(
            [
                "usage: xft <command> [options]",
                "",
                "commands:",
                "  recommend    run product recommendation",
                "  diligence    run enterprise due diligence",
                "  calibrate    run recommendation calibration",
                "  web          run Web enrichment or import Web cache",
                "  warehouse    build DuckDB warehouse or import Web cache",
                "  scenario     inspect or validate scenario bundles",
                "  runs         inspect generated run outputs",
                "  cache        sync or manage remote/local cache data",
                "",
                "examples:",
                '  xft recommend "企业名称"',
                '  xft diligence "企业名称" --dry-run',
                "  xft recommend --company-list company.txt --scenario config/scenarios/sales_recommendation",
                '  xft web enrich "企业名称"',
                "  xft runs inspect --output recommendation_runs_summary.csv",
                "  xft calibrate --company-list company.txt --labels calibration_labels.csv",
                "  xft warehouse build --input data --output cache/company_warehouse.duckdb",
                "  xft scenario validate config/scenarios/sales_recommendation",
                "",
            ]
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        _print_help()
        return 0
    command = args.pop(0)
    handler = COMMANDS.get(command)
    if handler is None:
        sys.stderr.write(f"error: unknown command: {command}\n")
        _print_help()
        return 2
    try:
        return handler(args)
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
