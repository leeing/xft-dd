from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from xft.pipeline.diligence.batch import _check_concurrency_limit, _dry_run_preview, parse_input_file
from xft.pipeline.diligence.config import AppConfig, BatchConfig, Dimension


def _make_cfg(batch_runs_dir: str = "batch_runs") -> AppConfig:
    return AppConfig(
        merge_prompt="综合{summaries}生成{target}的报告",
        dimensions=[
            Dimension(
                id="basic_info",
                name="工商基本信息",
                order=10,
                enabled=True,
                required=True,
                minimax_queries=["{target} 工商注册"],
                metaso_queries=["{target} 法定代表人"],
                metaso_mode="chat",
                summary_prompt="分析{target}\n{results}",
            )
        ],
        batch=BatchConfig(company_concurrency=1, batch_runs_dir=batch_runs_dir),
    )


def _cfg_file(tmp_path: Path, batch_runs_dir: str) -> Path:
    data = {
        "schema_version": "1.0",
        "model": "MiniMax-M2.7-Highspeed",
        "merge_prompt": "x",
        "dimensions": [
            {
                "id": "basic_info",
                "name": "工商基本信息",
                "order": 10,
                "enabled": True,
                "required": True,
                "minimax_queries": ["q"],
                "summary_prompt": "p\n{results}",
            },
        ],
        "batch": {
            "company_concurrency": 1,
            "continue_on_company_error": True,
            "skip_existing": True,
            "batch_runs_dir": batch_runs_dir,
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    return p


def test_parse_txt_ignores_empty_lines(tmp_path: Path) -> None:
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n\n公司B\n  \n公司C\n", encoding="utf-8")
    assert parse_input_file(str(f), name_column="name") == ["公司A", "公司B", "公司C"]


def test_parse_txt_deduplicates_warns(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n公司B\n公司A\n", encoding="utf-8")
    result = parse_input_file(str(f), name_column="name")
    assert result == ["公司A", "公司B"]
    assert "重复" in capsys.readouterr().err


def test_parse_csv_reads_name_column(tmp_path: Path) -> None:
    f = tmp_path / "companies.csv"
    f.write_text("company_name,region\n公司A,广东\n公司B,北京\n", encoding="utf-8")
    assert parse_input_file(str(f), name_column="company_name") == ["公司A", "公司B"]


def test_concurrency_limit_over_50_requires_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        _check_concurrency_limit(
            company_concurrency=2,
            dimension_concurrency=10,
            query_concurrency=5,
            force=False,
        )
    assert exc.value.code == 1


def test_concurrency_limit_over_50_with_flag_ok() -> None:
    # must not raise
    _check_concurrency_limit(
        company_concurrency=2,
        dimension_concurrency=10,
        query_concurrency=5,
        force=True,
    )


def test_concurrency_limit_warns_over_30(capsys: pytest.CaptureFixture) -> None:
    _check_concurrency_limit(
        company_concurrency=1,
        dimension_concurrency=10,
        query_concurrency=4,
        force=False,
    )
    assert "warning" in capsys.readouterr().err.lower() or "⚠️" in capsys.readouterr().err


def test_batch_dry_run_verbose_includes_metaso_queries(capsys: pytest.CaptureFixture) -> None:
    cfg = _make_cfg()
    _dry_run_preview(["公司A"], cfg.dimensions, cfg, verbose=True)
    err = capsys.readouterr().err
    assert "MiniMax Search" in err
    assert "Metaso (chat mode" in err
    assert "公司A 法定代表人" in err


async def test_batch_company_failure_does_not_stop_others(tmp_path: Path) -> None:
    from xft.pipeline.diligence.batch import run_batch

    batch_dir = str(tmp_path / "batch_runs")
    cfg = _make_cfg(batch_dir)
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n公司B\n", encoding="utf-8")
    cfg_path = str(_cfg_file(tmp_path, batch_dir))

    call_count = 0

    async def fake_run(target, config, output_dir, **kwargs):
        nonlocal call_count
        call_count += 1
        from xft.pipeline.diligence.models import CompanyRunResult

        if target == "公司A":
            msg = "公司A down"
            raise RuntimeError(msg)
        return CompanyRunResult(index=0, target=target, status="success", run_id="r")

    with patch("xft.pipeline.diligence.batch.run_company_graph", side_effect=fake_run):
        await run_batch(
            input_file=str(f),
            config=cfg,
            config_path=cfg_path,
            only=None,
            skip=None,
            dry_run=False,
            resume=False,
            batch_dir=None,
            force_high_concurrency=True,
            verbose=False,
            name_column="name",
        )

    assert call_count == 2


async def test_batch_resume_skips_completed(tmp_path: Path) -> None:
    import hashlib

    from xft.pipeline.diligence.batch import run_batch
    from xft.pipeline.diligence.models import BatchRunMeta, RunMeta

    batch_runs = tmp_path / "batch_runs"
    cfg = _make_cfg(str(batch_runs))
    companies = ["公司A", "公司B"]
    f = tmp_path / "companies.txt"
    f.write_text("\n".join(companies), encoding="utf-8")
    cfg_path = str(_cfg_file(tmp_path, str(batch_runs)))

    hash_a = hashlib.sha1("公司A".encode(), usedforsecurity=False).hexdigest()[:6]
    bd = batch_runs / "existing_batch"
    company_dir = bd / "companies" / f"001-{hash_a}"
    company_dir.mkdir(parents=True, exist_ok=True)
    (company_dir / "final_report.md").write_text("existing report")
    (company_dir / "run_meta.json").write_text(
        RunMeta(
            run_id="r",
            target="公司A",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            status="success",
            config_path="c",
            active_dimensions=["basic_info"],
        ).model_dump_json()
    )

    bm = BatchRunMeta(
        batch_id="existing_batch",
        index_target_map={1: "公司A", 2: "公司B"},
        total=2,
        success=1,
        partial=0,
        failed=0,
        skipped=0,
        started_at=datetime.now(UTC),
        config_path="c",
    )
    bd.mkdir(parents=True, exist_ok=True)
    (bd / "batch_meta.json").write_text(bm.model_dump_json())

    call_targets: list[str] = []

    async def fake_run(target, config, output_dir, **kwargs):
        call_targets.append(target)
        from xft.pipeline.diligence.models import CompanyRunResult

        return CompanyRunResult(index=0, target=target, status="success", run_id="r")

    with patch("xft.pipeline.diligence.batch.run_company_graph", side_effect=fake_run):
        await run_batch(
            input_file=str(f),
            config=cfg,
            config_path=cfg_path,
            only=None,
            skip=None,
            dry_run=False,
            resume=True,
            batch_dir=str(bd),
            force_high_concurrency=True,
            verbose=False,
            name_column="name",
        )

    assert "公司A" not in call_targets
    assert "公司B" in call_targets


async def test_batch_resume_mismatch_exits(tmp_path: Path) -> None:
    from xft.pipeline.diligence.batch import run_batch
    from xft.pipeline.diligence.models import BatchRunMeta

    batch_runs = tmp_path / "batch_runs"
    cfg = _make_cfg(str(batch_runs))
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n公司C\n", encoding="utf-8")
    cfg_path = str(_cfg_file(tmp_path, str(batch_runs)))

    bm = BatchRunMeta(
        batch_id="existing",
        index_target_map={1: "公司A", 2: "公司B"},
        total=2,
        success=1,
        partial=0,
        failed=0,
        skipped=0,
        started_at=datetime.now(UTC),
        config_path="c",
    )
    bd = batch_runs / "existing"
    bd.mkdir(parents=True, exist_ok=True)
    (bd / "batch_meta.json").write_text(bm.model_dump_json())

    exit_code = await run_batch(
        input_file=str(f),
        config=cfg,
        config_path=cfg_path,
        only=None,
        skip=None,
        dry_run=False,
        resume=True,
        batch_dir=str(bd),
        force_high_concurrency=True,
        verbose=False,
        name_column="name",
    )
    assert exit_code == 1


async def test_batch_produces_summary_files(tmp_path: Path) -> None:
    from xft.pipeline.diligence.batch import run_batch

    batch_runs = tmp_path / "batch_runs"
    cfg = _make_cfg(str(batch_runs))
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n公司B\n", encoding="utf-8")
    cfg_path = str(_cfg_file(tmp_path, str(batch_runs)))

    async def fake_run(target, config, output_dir, **kwargs):
        from xft.pipeline.diligence.models import CompanyRunResult

        return CompanyRunResult(index=0, target=target, status="success", run_id="r")

    with patch("xft.pipeline.diligence.batch.run_company_graph", side_effect=fake_run):
        await run_batch(
            input_file=str(f),
            config=cfg,
            config_path=cfg_path,
            only=None,
            skip=None,
            dry_run=False,
            resume=False,
            batch_dir=None,
            force_high_concurrency=True,
            verbose=False,
            name_column="name",
        )

    batch_dirs = list(batch_runs.iterdir())
    assert len(batch_dirs) == 1
    bd = batch_dirs[0]
    assert (bd / "batch_summary.csv").exists()
    assert (bd / "batch_summary.md").exists()
    assert (bd / "batch_meta.json").exists()


async def test_batch_dry_run_no_external_calls(tmp_path: Path) -> None:
    from xft.pipeline.diligence.batch import run_batch

    batch_runs = tmp_path / "batch_runs"
    cfg = _make_cfg(str(batch_runs))
    f = tmp_path / "companies.txt"
    f.write_text("公司A\n公司B\n", encoding="utf-8")
    cfg_path = str(_cfg_file(tmp_path, str(batch_runs)))

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with patch("xft.pipeline.diligence.batch.run_company_graph") as mock_run:
            exit_code = await run_batch(
                input_file=str(f),
                config=cfg,
                config_path=cfg_path,
                only=None,
                skip=None,
                dry_run=True,
                resume=False,
                batch_dir=None,
                force_high_concurrency=True,
                verbose=False,
                name_column="name",
            )

    mock_exec.assert_not_called()
    mock_run.assert_not_called()
    assert exit_code == 0
