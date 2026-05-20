from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xft.cli.main import main as xft_main


def test_xft_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "xft <command>" in captured.out
    assert "recommend" in captured.out
    assert "scenario" in captured.out
    assert "runs" in captured.out


def test_xft_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["missing"]) == 2
    captured = capsys.readouterr()
    assert "unknown command" in captured.err


def test_xft_scenario_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["scenario", "validate", "config/recommend/sales_recommendation"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["scenario_id"] == "sales_recommendation"
    assert payload["modules"] == 7
    assert "dimensions" not in payload


def test_xft_scenario_inspect_writes_output(tmp_path: Path) -> None:
    output = tmp_path / "scenario_resolved.json"

    assert xft_main(["scenario", "inspect", "config/recommend/sales_recommendation", "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["id"] == "sales_recommendation"
    assert "modules_path" in payload


def test_recommend_help() -> None:
    assert xft_main(["recommend", "--help"]) == 0


def test_xft_runs_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["runs", "--help"]) == 0
    captured = capsys.readouterr()
    assert "inspect" in captured.out


def test_xft_cache_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert xft_main(["cache", "--help"]) == 0
    captured = capsys.readouterr()
    assert "sync-remote" in captured.out


def test_recommend_smoke_command_uses_offline_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_recommendation(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            status="success",
            company_name=kwargs["company_name"],
            output_dir="recommendation_runs/smoke",
            report_path="recommendation_runs/smoke/report.md",
            result_path="recommendation_runs/smoke/result.json",
            error=None,
        )

    monkeypatch.setattr("xft.cli.recommend.run_recommendation", fake_run_recommendation)

    assert (
        xft_main(
            [
                "recommend",
                "--no-llm",
                "--warehouse",
                "cache/company_warehouse.duckdb",
                "--scenario",
                "config/recommend/sales_recommendation",
                "--llm-debug",
                "--llm-concurrency",
                "2",
                "--with-web",
                "--web-refresh",
                "--web-provider",
                "fake_search",
                "烟测公司",
            ]
        )
        == 0
    )
    assert captured["company_name"] == "烟测公司"
    assert captured["use_llm"] is False
    assert captured["llm_debug"] is True
    assert captured["llm_concurrency"] == 2
    assert captured["with_web"] is True
    assert captured["refresh_web"] is True
    assert captured["web_providers"] == ["fake_search"]
