from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


def _cfg_file(tmp_path: Path) -> Path:
    data = {
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
                "metaso_queries": ["{target} 统一社会信用代码"],
                "metaso_mode": "search",
                "metaso_search_size": 2,
                "summary_prompt": "{target}\n{results}",
            }
        ],
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    return p


def _run_main(args: list[str]) -> subprocess.CompletedProcess:
    root = Path(__file__).parent.parent
    return subprocess.run(
        [sys.executable, "-m", "xft.cli.main", "diligence", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
    )


def test_empty_target_exits_1() -> None:
    result = _run_main([""])
    assert result.returncode == 1


def test_batch_and_positional_mutually_exclusive() -> None:
    result = _run_main(["某公司", "--batch", "companies.txt"])
    assert result.returncode == 1


def test_cli_default_config_is_directory() -> None:
    import importlib
    import sys as _sys

    root = str(Path(__file__).parent.parent)
    if root not in _sys.path:
        _sys.path.insert(0, root)

    main_module = importlib.import_module("xft.cli.diligence")
    with patch.object(sys, "argv", ["xft diligence", "某公司"]):
        args = main_module._parse_args()
    assert args.config == "config"


def test_dry_run_no_external_calls(tmp_path: Path) -> None:
    cfg = _cfg_file(tmp_path)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with patch("xft.nodes.summarize_node.get_ai_client") as mock_ai:
            import asyncio

            from xft.config import load_config
            from xft.cli import diligence as diligence_cli

            config = load_config(str(cfg))
            exit_code = asyncio.run(
                diligence_cli.run_dry_run(
                    target="某公司",
                    config=config,
                    only=None,
                    skip=None,
                )
            )
    mock_exec.assert_not_called()
    mock_ai.assert_not_called()
    assert exit_code == 0


def test_dry_run_includes_metaso_queries(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    cfg = _cfg_file(tmp_path)
    import asyncio

    from xft.config import load_config
    from xft.cli import diligence as diligence_cli

    config = load_config(str(cfg))
    exit_code = asyncio.run(
        diligence_cli.run_dry_run(
            target="某公司",
            config=config,
            only=None,
            skip=None,
        )
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MiniMax Search" in captured.err
    assert "Metaso (search mode, size=2)" in captured.err
    assert "某公司 统一社会信用代码" in captured.err


def test_only_filter_limits_dimensions() -> None:
    from xft.config import AppConfig, Dimension

    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["q"],
                summary_prompt="p\n{results}",
            ),
            Dimension(
                id="ip",
                name="知识产权",
                order=60,
                enabled=True,
                required=False,
                minimax_queries=["q"],
                summary_prompt="p\n{results}",
            ),
        ],
    )
    only = ["ip"]
    filtered = [d for d in cfg.dimensions if d.id in only]
    assert len(filtered) == 1
    assert filtered[0].id == "ip"


def test_validate_args_target_too_long() -> None:
    """Target longer than 200 chars should be rejected."""
    result = _run_main(["A" * 201])
    assert result.returncode == 1
    assert "too long" in result.stderr


def test_validate_args_target_exactly_max_len() -> None:
    """Target of exactly 200 chars should not fail length validation."""
    import argparse
    import importlib
    import sys as _sys

    root = str(Path(__file__).parent.parent)
    if root not in _sys.path:
        _sys.path.insert(0, root)

    main_module = importlib.import_module("xft.cli.diligence")
    args = argparse.Namespace(batch=None, target="A" * 200)
    assert main_module._validate_args(args) is None


def test_skip_filter_removes_dimension() -> None:
    from xft.config import AppConfig, Dimension

    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["q"],
                summary_prompt="p\n{results}",
            ),
            Dimension(
                id="listing",
                name="上市情况",
                order=80,
                enabled=True,
                required=False,
                minimax_queries=["q"],
                summary_prompt="p\n{results}",
            ),
        ],
    )
    skip = ["listing"]
    filtered = [d for d in cfg.dimensions if d.id not in skip]
    assert len(filtered) == 1
    assert filtered[0].id == "basic_info"
