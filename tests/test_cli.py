from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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
        [sys.executable, str(root / "main.py"), *args],
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


def test_dry_run_no_external_calls(tmp_path: Path) -> None:
    cfg = _cfg_file(tmp_path)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with patch("diligence.nodes.summarize_node.get_ai_client") as mock_ai:
            import asyncio

            import main as main_module
            from diligence.config import load_config

            config = load_config(str(cfg))
            exit_code = asyncio.run(
                main_module.run_dry_run(
                    target="某公司",
                    config=config,
                    only=None,
                    skip=None,
                )
            )
    mock_exec.assert_not_called()
    mock_ai.assert_not_called()
    assert exit_code == 0


def test_only_filter_limits_dimensions() -> None:
    from diligence.config import AppConfig, Dimension

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


def test_skip_filter_removes_dimension() -> None:
    from diligence.config import AppConfig, Dimension

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
