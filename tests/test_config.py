from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from xft.config import load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _write_config_dir(tmp_path: Path) -> Path:
    root = tmp_path / "config"
    (root / "dimensions").mkdir(parents=True)
    (root / "prompts" / "dimensions").mkdir(parents=True)
    (root / "app.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: "1.0"
            dimension_concurrency: 3
            """
        ),
        encoding="utf-8",
    )
    (root / "prompts" / "merge.md").write_text(
        "请综合{summaries}生成{target}的报告",
        encoding="utf-8",
    )
    (root / "prompts" / "summarize_system.md").write_text(
        "目录化 summarize system",
        encoding="utf-8",
    )
    (root / "prompts" / "dimensions" / "basic_info.md").write_text(
        "目录化 summary prompt: {target}\\n{results}",
        encoding="utf-8",
    )
    (root / "dimensions" / "10_basic_info.yaml").write_text(
        textwrap.dedent(
            """
            id: basic_info
            name: 工商基本信息
            order: 10
            enabled: true
            required: true
            minimax_queries:
              - "{target} 工商注册信息"
            summary_prompt_file: ../prompts/dimensions/basic_info.md
            """
        ),
        encoding="utf-8",
    )
    return root


MINIMAL_CONFIG = """
schema_version: "1.0"
merge_prompt: "请综合{summaries}生成{target}的报告"
dimensions:
  - id: basic_info
    name: 工商基本信息
    order: 10
    enabled: true
    required: true
    minimax_queries:
      - "{target} 工商注册信息"
    summary_prompt: "请从以下结果中提取{target}的工商信息。\\n{results}"
"""


def test_load_config_valid(tmp_path: Path) -> None:
    p = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(str(p))
    assert len(cfg.dimensions) == 1
    assert cfg.dimensions[0].id == "basic_info"
    assert cfg.dimensions[0].required is True


def test_load_config_file_still_supported(tmp_path: Path) -> None:
    p = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(str(p))
    assert cfg.merge_prompt.startswith("请综合")
    assert cfg.dimensions[0].summary_prompt.startswith("请从以下结果")


def test_load_config_dir_success(tmp_path: Path) -> None:
    root = _write_config_dir(tmp_path)
    cfg = load_config(str(root))
    assert cfg.dimension_concurrency == 3
    assert cfg.merge_prompt == "请综合{summaries}生成{target}的报告"
    assert cfg.summarize_system_prompt == "目录化 summarize system"
    assert cfg.dimensions[0].id == "basic_info"
    assert cfg.dimensions[0].summary_prompt.startswith("目录化 summary prompt")


def test_load_config_dir_missing_app_yaml(tmp_path: Path) -> None:
    root = tmp_path / "config"
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        load_config(str(root))


def test_load_config_dir_missing_merge_prompt(tmp_path: Path) -> None:
    root = _write_config_dir(tmp_path)
    (root / "prompts" / "merge.md").unlink()
    with pytest.raises(FileNotFoundError):
        load_config(str(root))


def test_load_config_dir_empty_dimensions(tmp_path: Path) -> None:
    root = _write_config_dir(tmp_path)
    for path in (root / "dimensions").glob("*.yaml"):
        path.unlink()
    with pytest.raises(ValueError, match="no dimension"):
        load_config(str(root))


def test_load_config_dir_missing_dimension_prompt(tmp_path: Path) -> None:
    root = _write_config_dir(tmp_path)
    (root / "prompts" / "dimensions" / "basic_info.md").unlink()
    with pytest.raises(FileNotFoundError):
        load_config(str(root))


def test_load_config_dir_duplicate_dimension_id(tmp_path: Path) -> None:
    root = _write_config_dir(tmp_path)
    (root / "dimensions" / "20_duplicate.yaml").write_text(
        textwrap.dedent(
            """
            id: basic_info
            name: 重复维度
            order: 20
            enabled: true
            required: false
            minimax_queries:
              - "{target} 重复"
            summary_prompt: "{target}\\n{results}"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="duplicate dimension"):
        load_config(str(root))


def test_active_dimensions_excludes_disabled(tmp_path: Path) -> None:
    content = (
        MINIMAL_CONFIG
        + """
  - id: industry
    name: 行业与细分
    order: 20
    enabled: false
    required: false
    minimax_queries:
      - "{target} 行业"
    summary_prompt: "{target}\\n{results}"
"""
    )
    p = _write_config(tmp_path, content)
    cfg = load_config(str(p))
    active = [d for d in cfg.dimensions if d.enabled]
    assert len(active) == 1
    assert active[0].id == "basic_info"


def test_load_config_missing_required_field(tmp_path: Path) -> None:
    p = _write_config(tmp_path, 'schema_version: "1.0"\nmerge_prompt: "p"\n')
    with pytest.raises(ValidationError):  # noqa: PT011
        load_config(str(p))


def test_load_config_schema_version_mismatch_warns(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    content = MINIMAL_CONFIG.replace('"1.0"', '"99.0"')
    p = _write_config(tmp_path, content)
    load_config(str(p))  # must not raise
    captured = capsys.readouterr()
    assert "schema_version" in captured.err or "99.0" in captured.err


def test_dimension_order_sorted(tmp_path: Path) -> None:
    content = """
schema_version: "1.0"
merge_prompt: "x"
dimensions:
  - id: ip
    name: 知识产权
    order: 60
    enabled: true
    required: false
    minimax_queries: ["{target} 专利"]
    summary_prompt: "{target}\\n{results}"
  - id: basic_info
    name: 工商基本信息
    order: 10
    enabled: true
    required: true
    minimax_queries: ["{target} 工商"]
    summary_prompt: "{target}\\n{results}"
"""
    p = _write_config(tmp_path, content)
    cfg = load_config(str(p))
    assert cfg.dimensions[0].id == "basic_info"
    assert cfg.dimensions[1].id == "ip"
