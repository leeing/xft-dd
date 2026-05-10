from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from diligence.config import load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


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
