from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from xft.cli.main import main as xft_main


ROOT = Path(__file__).parent.parent


def test_xft_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "xft <command>" in captured.out
    assert "recommend" in captured.out
    assert "diligence" in captured.out
    assert "scenario" in captured.out
    assert "runs" in captured.out


def test_xft_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["missing"]) == 2
    captured = capsys.readouterr()
    assert "unknown command" in captured.err


def test_xft_scenario_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["scenario", "validate", "config/scenarios/sales_recommendation"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["scenario_id"] == "sales_recommendation"
    assert payload["products"] > 0
    assert payload["dimensions"] > 0


def test_xft_scenario_inspect_writes_output(tmp_path: Path) -> None:
    output = tmp_path / "scenario_resolved.json"

    assert xft_main(["scenario", "inspect", "config/scenarios/bank_marketing", "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["id"] == "bank_marketing"
    assert len(payload["products_effective_hash"]) == 64


def test_compatibility_wrapper_help() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/compat/run_recommender.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--scenario" in result.stdout


def test_xft_runs_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["runs", "--help"]) == 0
    captured = capsys.readouterr()
    assert "inspect" in captured.out


def test_xft_cache_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["cache", "--help"]) == 0
    captured = capsys.readouterr()
    assert "sync-remote" in captured.out
