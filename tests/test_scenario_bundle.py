from __future__ import annotations

import json
from pathlib import Path

import pytest

from diligence.recommender.config_loader import load_dimensions_config, load_products_config
from diligence.recommender.graph import run_recommendation
from diligence.recommender.scenario import load_scenario
from diligence.recommender.web.config_loader import load_web_extract_llm_config, load_web_search_config
from diligence.warehouse.prophet_loader import load_prophet_data


SCENARIO_DIR = Path("config/scenarios/sales_recommendation")


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
    assert scenario.products_path.endswith("config/scenarios/sales_recommendation/products.yaml")
    assert scenario.prompt_paths["match_system"].endswith("prompts/match_system.md")


def test_config_loaders_accept_scenario_directory() -> None:
    products = load_products_config(SCENARIO_DIR)
    dimensions = load_dimensions_config(SCENARIO_DIR)
    web_config = load_web_search_config(SCENARIO_DIR)
    extract_config = load_web_extract_llm_config(SCENARIO_DIR)

    assert products.products
    assert dimensions.dimensions
    assert web_config.cache_root.endswith("data/web/sales_recommendation")
    assert extract_config.prompt_file.endswith("prompts/extract_evidence_system.md")


@pytest.mark.asyncio
async def test_run_recommendation_accepts_scenario_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("diligence.settings.settings.llm_api_key", "")
    monkeypatch.setattr("diligence.settings.settings.minimax_api_key", "")
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
    payload = json.loads((Path(result.output_dir) / "result.json").read_text(encoding="utf-8"))
    assert payload["scenario"] == "sales_recommendation"
    assert payload["scenario_name"] == "销售产品推荐"
    assert payload["recommendations"]
