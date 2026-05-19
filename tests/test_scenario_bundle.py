from __future__ import annotations

import json
from pathlib import Path

import pytest

from xft.core.config_loader import load_dimensions_config
from xft.pipeline.recommender.graph import run_recommendation
from xft.core.scenario import load_scenario
from xft.evidence.policy import load_evidence_policy
from xft.pipeline.recommender.business_config_loader import load_business_recommendation_config
from xft.web.config_loader import load_web_extract_llm_config, load_web_search_config
from xft.warehouse.prophet_loader import load_prophet_data


SCENARIO_DIR = Path("config/recommend/sales_recommendation")


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


def test_load_scenario_resolves_bundle_paths() -> None:
    scenario = load_scenario(SCENARIO_DIR)

    assert scenario is not None
    assert scenario.config.id == "sales_recommendation"
    assert scenario.evidence_policy_path.endswith("config/recommend/sales_recommendation/evidence_policy.yaml")
    assert scenario.business_modules_path is not None
    assert scenario.business_modules_path.endswith("config/recommend/sales_recommendation/business_modules.yaml")
    assert scenario.prompt_paths["web_extract_system"].endswith("prompts/extract_evidence_system.md")


def test_scenario_extends_and_writes_resolved_config(tmp_path: Path) -> None:
    parent = tmp_path / "base"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    (parent / "scenario.yaml").write_text(
        """
version: "1.0"
id: base_sales
name: 基础销售场景
dimensions_config: analysis_dimensions.yaml
web_search_config: web_search.yaml
web_extract_llm_config: web_extract_llm.yaml
prompts:
  web_extract_system: prompts/extract.md
output_dir: runs/base
web_cache_root: web/base
""",
        encoding="utf-8",
    )
    (child / "scenario.yaml").write_text(
        """
extends: ../base
id: child_sales
name: 子销售场景
overrides:
  prompts:
    business_system: prompts/business.md
  output_dir: runs/child
""",
        encoding="utf-8",
    )

    scenario = load_scenario(child)

    assert scenario is not None
    assert scenario.config.id == "child_sales"
    assert scenario.prompt_paths["web_extract_system"].endswith("base/prompts/extract.md")
    assert scenario.prompt_paths["business_system"].endswith("child/prompts/business.md")
    assert scenario.output_dir is not None
    assert scenario.output_dir.endswith("child/runs/child")
    resolved_path = scenario.write_resolved_config(tmp_path / "scenario_resolved.json")
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    assert resolved["id"] == "child_sales"
    assert resolved["prompt_paths"]["business_system"].endswith("child/prompts/business.md")


def test_config_loaders_accept_scenario_directory() -> None:
    dimensions = load_dimensions_config(SCENARIO_DIR)
    web_config = load_web_search_config(SCENARIO_DIR)
    extract_config = load_web_extract_llm_config(SCENARIO_DIR)
    evidence_policy = load_evidence_policy(SCENARIO_DIR)
    business = load_business_recommendation_config(SCENARIO_DIR)

    assert dimensions.dimensions
    assert web_config.cache_root.endswith("data/web/sales_recommendation")
    assert extract_config.prompt_file.endswith("prompts/extract_evidence_system.md")
    assert evidence_policy.web_planning.supported_facts_to_skip_web == 3
    assert len(business.modules) == 7


@pytest.mark.asyncio
async def test_run_recommendation_accepts_scenario_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("xft.settings.settings.llm_api_key", "")
    monkeypatch.setattr("xft.settings.settings.minimax_api_key", "")
    warehouse = _build_warehouse(tmp_path)

    result = await run_recommendation(
        company_name="广东德美精细化工集团股份有限公司",
        warehouse_db=str(warehouse),
        scenario_path=str(SCENARIO_DIR),
        output_dir=str(tmp_path / "runs"),
        run_id="scenario-run",
        use_llm=False,
    )

    assert result.status in ("success", "partial")
    output_dir = Path(result.output_dir)
    assert not (output_dir / "internal_result.json").exists()
    assert not (output_dir / "match_results.json").exists()
    business_payload = json.loads((Path(result.output_dir) / "result.json").read_text(encoding="utf-8"))
    assert business_payload["CompanyName"] == "广东德美精细化工集团股份有限公司"
    assert "AcceptanceResult" in business_payload
    llm_metrics = json.loads((Path(result.output_dir) / "llm_metrics.json").read_text(encoding="utf-8"))
    assert llm_metrics["total"] == 0
    assert (Path(result.output_dir) / "llm_calls.jsonl").exists()
    resolved = json.loads((Path(result.output_dir) / "scenario_resolved.json").read_text(encoding="utf-8"))
    assert resolved["id"] == "sales_recommendation"
    manifest = json.loads((Path(result.output_dir) / "config_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pipeline"] == "recommender"
    assert manifest["scenario_id"] == "sales_recommendation"
    assert "products" not in manifest["files"]
    assert "scoring_policy" not in manifest["files"]
    assert len(manifest["effective_hashes"]["dimensions"]) == 64
