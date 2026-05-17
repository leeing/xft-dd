from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from diligence.models import SearchItem, make_item_id
from diligence.web.config_loader import load_web_search_config
from diligence.web.models import ProviderSearchResponse
from diligence.web.planner import plan_web_search
from diligence.web.runner import run_web_enrichment
from diligence.web.web_loader import load_web_cache_to_duckdb
from diligence.warehouse.prophet_loader import load_prophet_data


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _build_warehouse(tmp_path: Path) -> Path:
    input_root = tmp_path / "data"
    company = input_root / "91440606707539050R_广东德美精细化工集团股份有限公司"
    _write_json(
        company / ".meta.json",
        {"company_name": "广东德美精细化工集团股份有限公司", "credit_code": "91440606707539050R", "fetchers": {}},
    )
    _write_json(
        company / "info.json",
        {
            "data": {
                "info": {
                    "info": {
                        "name": "广东德美精细化工集团股份有限公司",
                        "unifiedSocialCreditCode": "91440606707539050R",
                        "cate1": "制造业",
                        "businessScope": "精细化学品生产销售",
                    }
                }
            }
        },
    )
    _write_json(
        company / "query_company.json",
        {"data": {"entName": "广东德美精细化工集团股份有限公司", "employeeNum": 300, "idtCtgNm": "制造业"}},
    )
    _write_json(company / "label.json", {"labels": ["高质量客户"], "raw_label_codes": []})
    _write_json(company / "intellectual.json", {"data": {"intellectual": []}})
    _write_json(company / "risk_insight.json", {"data": {"riskCount": {"selfRisk": 1}}})
    _write_json(company / "recruit_message.json", {"data": {"list": []}})
    _write_json(company / "query_bidding_total.json", {"data": {"total": 1}})
    _write_json(company / "query_qualification.json", {"data": []})
    _write_json(company / "staff.json", {"data": {"list": []}})
    _write_json(company / "shareholder.json", {"data": {"list": []}})
    db = tmp_path / "warehouse.duckdb"
    load_prophet_data(input_root=input_root, output_db=db)
    return db


def _write_web_config(tmp_path: Path) -> Path:
    path = tmp_path / "web_search.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "enabled": True,
                "cache_root": str(tmp_path / "web"),
                "default_providers": ["fake_search"],
                "providers": {
                    "fake_search": {
                        "type": "minimax",
                        "enabled": True,
                        "max_results": 2,
                        "timeout_seconds": 3,
                    }
                },
                "execution": {
                    "max_queries_per_dimension": 1,
                    "max_results_per_query": 2,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_dimensions_config(tmp_path: Path) -> Path:
    path = tmp_path / "dimensions.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "dimensions": [
                    {
                        "id": "supply_chain_procurement",
                        "level1": "供应链与采购管理",
                        "level2": "采购规模与特征",
                        "level3": "供应链复杂度",
                        "role": "供应链专家",
                        "evidence_templates": [{"field": "industry", "label": "行业"}],
                        "insufficient_evidence": ["供应商数量"],
                        "web_search_queries": ["{company_name} 供应商", "{company_name} 采购"],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_supported_dimensions_config(tmp_path: Path) -> Path:
    path = tmp_path / "supported_dimensions.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "dimensions": [
                    {
                        "id": "basic_profile",
                        "level1": "企业基础信息与股权结构",
                        "level2": "基础特征",
                        "level3": "主体规模",
                        "role": "尽调专家",
                        "evidence_templates": [
                            {"field": "industry", "label": "行业"},
                            {"field": "employee_count", "label": "员工规模"},
                            {"field": "business_scope", "label": "经营范围"},
                        ],
                        "insufficient_evidence": ["实际控制人"],
                        "web_search_queries": ["{company_name} 股权结构"],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


class _FakeProvider:
    name = "fake_search"

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        item = SearchItem(
            id=make_item_id(url="https://example.com/a", title="供应商新闻", snippet="企业与供应商协同。"),
            title="供应商新闻",
            url="https://example.com/a",
            snippet="企业与供应商协同。",
            query=query,
            dimension_id=dimension_id,
            source="minimax",
            rank=0,
            fetched_at=datetime.now(UTC),
        )
        return ProviderSearchResponse(
            provider=self.name,
            provider_type="minimax",
            query=query,
            dimension_id=dimension_id,
            status="success",
            items=[item.model_dump()],
        )


def test_web_search_config_loads(tmp_path: Path) -> None:
    cfg = load_web_search_config(_write_web_config(tmp_path))
    assert cfg.default_providers == ["fake_search"]
    assert cfg.providers["fake_search"].type == "minimax"


def test_default_web_search_config_uses_minimax() -> None:
    cfg = load_web_search_config("config/recommender/web_search.yaml")

    assert cfg.default_providers == ["minimax_search"]
    assert cfg.providers["minimax_search"].enabled is True
    assert cfg.execution.fetch_pages is True


def test_planner_skips_supported_dimensions() -> None:
    from diligence.pipeline.recommender.models import DimensionAnalysis, EvidenceFact

    analysis = DimensionAnalysis(
        dimension_id="basic_profile",
        title="企业基础画像",
        status="supported",
        confidence="中",
        facts=[
            EvidenceFact(claim="行业：制造业", source_fields=["industry"]),
            EvidenceFact(claim="员工规模：300", source_fields=["employee_count"]),
            EvidenceFact(claim="上市状态：是", source_fields=["is_listed"]),
        ],
        web_search_queries=["测试公司 股权结构"],
    )

    plan = plan_web_search([analysis], max_queries_per_dimension=3)
    forced = plan_web_search([analysis], force_dimensions=True, max_queries_per_dimension=3)

    assert not plan.planned
    assert plan.skipped[0].reason == "local_dimension_supported"
    assert forced.planned[0].analysis.dimension_id == "basic_profile"


@pytest.mark.asyncio
async def test_run_web_enrichment_writes_cache_and_loads_duckdb(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    warehouse = _build_warehouse(tmp_path)
    web_config = _write_web_config(tmp_path)
    dimensions_config = _write_supported_dimensions_config(tmp_path)

    def fake_build_provider(_name: str, _config: Any) -> _FakeProvider:
        return _FakeProvider()

    monkeypatch.setattr("diligence.web.runner.build_provider", fake_build_provider)

    result = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-test",
        load_to_duckdb=True,
        force_dimensions=True,
        use_llm_extraction=False,
    )

    assert result.status == "success"
    assert result.queries == 1
    assert result.results == 1
    assert result.evidence == 1
    out = Path(result.output_dir)
    assert (out / "manifest.json").exists()
    assert (out / "queries.jsonl").exists()
    assert (out / "search_results.jsonl").exists()
    assert (out / "web_evidence.jsonl").exists()
    assert (out / "plan.json").exists()
    assert (out / "web_cache_report.json").exists()
    assert (out / "web_cache_report.md").exists()
    assert (out.parent / "cache_index.json").exists()
    cache_index = json.loads((out.parent / "cache_index.json").read_text(encoding="utf-8"))
    assert cache_index["schema_version"] == "1.1"
    assert cache_index["queries"][0]["key_hash"]
    assert "extractions" in cache_index
    assert list((out / "provider_responses").glob("*.json"))

    conn = duckdb.connect(str(warehouse), read_only=True)
    try:
        assert conn.execute("select count(*) from web_search_runs").fetchone()[0] == 1
        assert conn.execute("select count(*) from web_search_queries").fetchone()[0] == 1
        assert conn.execute("select count(*) from web_search_results").fetchone()[0] == 1
        claim = conn.execute("select claim from web_evidence").fetchone()[0]
        assert "供应商新闻" in claim
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_run_web_enrichment_extract_only_reuses_cached_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    warehouse = _build_warehouse(tmp_path)
    web_config = _write_web_config(tmp_path)
    dimensions_config = _write_supported_dimensions_config(tmp_path)
    calls = 0

    def fake_build_provider(_name: str, _config: Any) -> _FakeProvider:
        nonlocal calls
        calls += 1
        return _FakeProvider()

    monkeypatch.setattr("diligence.web.runner.build_provider", fake_build_provider)

    first = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-source",
        load_to_duckdb=False,
        force_dimensions=True,
        use_llm_extraction=False,
        fetch_pages=False,
    )

    assert first.status == "success"
    assert calls == 1

    def fail_build_provider(_name: str, _config: Any) -> _FakeProvider:
        msg = "provider should not be called in extract-only mode"
        raise AssertionError(msg)

    monkeypatch.setattr("diligence.web.runner.build_provider", fail_build_provider)

    second = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-extract-only",
        load_to_duckdb=False,
        force_dimensions=True,
        use_llm_extraction=False,
        fetch_pages=False,
        extract_only=True,
        source_run_id="web-source",
    )

    assert second.status == "success"
    assert second.queries == 1
    assert second.results == 1
    assert second.evidence == 1
    out = Path(second.output_dir)
    assert (out / "queries.jsonl").exists()
    assert (out / "search_results.jsonl").exists()
    assert (out / "web_evidence.jsonl").exists()
    report = json.loads((out / "web_cache_report.json").read_text(encoding="utf-8"))
    assert report["cache"]["search_reused"] == 1
    assert report["cache"]["extraction_reused"] == 1


@pytest.mark.asyncio
async def test_run_web_enrichment_provider_config_change_invalidates_search_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    warehouse = _build_warehouse(tmp_path)
    web_config = _write_web_config(tmp_path)
    dimensions_config = _write_supported_dimensions_config(tmp_path)
    calls = 0

    def fake_build_provider(_name: str, _config: Any) -> _FakeProvider:
        nonlocal calls
        calls += 1
        return _FakeProvider()

    monkeypatch.setattr("diligence.web.runner.build_provider", fake_build_provider)

    first = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-source",
        load_to_duckdb=False,
        force_dimensions=True,
        use_llm_extraction=False,
        fetch_pages=False,
    )
    assert first.status == "success"
    assert calls == 1

    config_data = yaml.safe_load(web_config.read_text(encoding="utf-8"))
    config_data["providers"]["fake_search"]["timeout_seconds"] = 9
    web_config.write_text(yaml.safe_dump(config_data, allow_unicode=True), encoding="utf-8")

    second = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-config-changed",
        load_to_duckdb=False,
        force_dimensions=True,
        use_llm_extraction=False,
        fetch_pages=False,
        source_run_id="web-source",
    )

    assert second.status == "success"
    assert calls == 2
    report = json.loads((Path(second.output_dir) / "web_cache_report.json").read_text(encoding="utf-8"))
    assert report["cache"]["search_reused"] == 0
    assert report["cache"]["search_executed"] == 1


@pytest.mark.asyncio
async def test_run_web_enrichment_skips_supported_dimension(tmp_path: Path) -> None:
    warehouse = _build_warehouse(tmp_path)
    web_config = _write_web_config(tmp_path)
    dimensions_config = _write_supported_dimensions_config(tmp_path)

    result = await run_web_enrichment(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        web_config_path=str(web_config),
        dimensions_config_path=str(dimensions_config),
        output_root=str(tmp_path / "web"),
        run_id="web-skip",
        load_to_duckdb=False,
        use_llm_extraction=False,
    )

    out = Path(result.output_dir)
    assert result.queries == 0
    assert (out / "skipped_queries.jsonl").exists()
    skipped = (out / "skipped_queries.jsonl").read_text(encoding="utf-8")
    assert "local_dimension_supported" in skipped


def test_web_loader_rebuilds_from_cache(tmp_path: Path) -> None:
    warehouse = _build_warehouse(tmp_path)
    run_dir = tmp_path / "web" / "91440606707539050R_广东德美精细化工集团股份有限公司" / "web-test"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "web_run_id": "web-test",
                "company_name": "广东德美精细化工集团股份有限公司",
                "credit_code": "91440606707539050R",
                "warehouse_db": str(warehouse),
                "created_at": "2026-05-16T00:00:00Z",
                "config": {},
                "providers": ["fake_search"],
                "dimensions": ["supply_chain_procurement"],
                "status": "success",
                "errors": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "queries.jsonl").write_text(
        json.dumps(
            {
                "query_id": "q_0001",
                "web_run_id": "web-test",
                "credit_code": "91440606707539050R",
                "company_name": "广东德美精细化工集团股份有限公司",
                "dimension_id": "supply_chain_procurement",
                "provider": "fake_search",
                "query": "广东德美 供应商",
                "status": "success",
                "raw_response_path": "provider_responses/fake.json",
                "created_at": "2026-05-16T00:00:00Z",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "search_results.jsonl").write_text("", encoding="utf-8")
    (run_dir / "web_evidence.jsonl").write_text("", encoding="utf-8")

    summary = load_web_cache_to_duckdb(input_root=tmp_path / "web", warehouse_db=warehouse, rebuild=True)

    assert summary.runs == 1
    assert summary.queries == 1
    assert summary.table_rows["web_search_runs"] == 1
    assert summary.table_rows["web_search_queries"] == 1
