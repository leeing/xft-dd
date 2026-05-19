from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from xft.cli.main import main as xft_main


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
    assert xft_main(["scenario", "validate", "config/recommend/sales_recommendation"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["scenario_id"] == "sales_recommendation"
    assert payload["business_modules"] == 7
    assert payload["dimensions"] > 0


def test_xft_scenario_inspect_writes_output(tmp_path: Path) -> None:
    output = tmp_path / "scenario_resolved.json"

    assert xft_main(["scenario", "inspect", "config/recommend/bank_marketing", "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["id"] == "bank_marketing"
    assert "business_modules_path" in payload


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
                "烟测公司",
            ]
        )
        == 0
    )
    assert captured["company_name"] == "烟测公司"
    assert captured["use_llm"] is False
    assert captured["with_web"] is False
    assert captured["use_web_evidence"] is False
    assert captured["llm_debug"] is True
    assert captured["llm_concurrency"] == 2


def test_diligence_smoke_command_dry_run_no_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "model": "MiniMax-M2.7-Highspeed",
                "merge_prompt": "综合{summaries}生成{target}的报告",
                "dimensions": [
                    {
                        "id": "basic_info",
                        "name": "工商基本信息",
                        "order": 10,
                        "enabled": True,
                        "required": True,
                        "minimax_queries": ["{target} 工商注册"],
                        "summary_prompt": "{target}\n{results}",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    async def fail_external_call(*_args: object, **_kwargs: object) -> None:
        message = "diligence smoke dry-run must not call external processes"
        raise AssertionError(message)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fail_external_call)

    assert xft_main(["diligence", "--config", str(config_path), "--dry-run", "烟测公司"]) == 0
    captured = capsys.readouterr()
    assert "dry-run complete" in captured.err
