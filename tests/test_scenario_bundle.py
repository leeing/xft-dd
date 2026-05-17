from __future__ import annotations

import json
from pathlib import Path

import pytest

from xft.pipeline.recommender.config_loader import (
    load_dimensions_config,
    load_products_config,
    write_products_resolved_config,
)
from xft.pipeline.recommender.graph import run_recommendation
from xft.core.scenario import load_scenario
from xft.evidence.policy import load_evidence_policy
from xft.scoring.policy_loader import load_scoring_policy
from xft.web.config_loader import load_web_extract_llm_config, load_web_search_config
from xft.warehouse.prophet_loader import load_prophet_data


SCENARIO_DIR = Path("config/scenarios/sales_recommendation")
BANK_SCENARIO_DIR = Path("config/scenarios/bank_marketing")


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
    assert scenario.scoring_policy_path.endswith("config/scenarios/sales_recommendation/scoring_policy.yaml")
    assert scenario.evidence_policy_path.endswith("config/scenarios/sales_recommendation/evidence_policy.yaml")
    assert scenario.prompt_paths["match_system"].endswith("prompts/match_system.md")


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
products_config: products.yaml
dimensions_config: analysis_dimensions.yaml
web_search_config: web_search.yaml
web_extract_llm_config: web_extract_llm.yaml
prompts:
  match_system: prompts/match.md
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
    recommend_system: prompts/recommend.md
  output_dir: runs/child
""",
        encoding="utf-8",
    )

    scenario = load_scenario(child)

    assert scenario is not None
    assert scenario.config.id == "child_sales"
    assert scenario.config.products_config.endswith("base/products.yaml")
    assert scenario.prompt_paths["match_system"].endswith("base/prompts/match.md")
    assert scenario.prompt_paths["recommend_system"].endswith("child/prompts/recommend.md")
    assert scenario.output_dir is not None
    assert scenario.output_dir.endswith("child/runs/child")
    resolved_path = scenario.write_resolved_config(tmp_path / "scenario_resolved.json")
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    assert resolved["id"] == "child_sales"
    assert resolved["products_path"].endswith("base/products.yaml")
    assert resolved["prompt_paths"]["recommend_system"].endswith("child/prompts/recommend.md")


def test_scenario_products_patch_applies_set_append_replace_remove(tmp_path: Path) -> None:
    parent = tmp_path / "base"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    (parent / "scenario.yaml").write_text(
        """
version: "1.0"
id: base_sales
name: 基础销售场景
products_config: products.yaml
dimensions_config: analysis_dimensions.yaml
web_search_config: web_search.yaml
web_extract_llm_config: web_extract_llm.yaml
""",
        encoding="utf-8",
    )
    (parent / "products.yaml").write_text(
        """
version: "1.0"
scenario: product_recommendation
output_dir: runs
products:
  - module_id: crm_channel
    module_name: 客户与渠道管理(CRM)
    priority: 82
    base_score: 46
    target_needs: [sales_channel]
    match_rule: 原始规则
    positive_rules:
      - id: sales_channel_supported
        dimension_id: sales_channel
        evidence_type: supported
        weight: 16
        reason: 原始销售渠道规则
      - id: old_rule
        source_field: old_field
        op: exists
        weight: 1
        reason: 待删除规则
    negative_rules:
      - id: missing_channel
        missing_evidence: 销售渠道
        penalty: 5
        reason: 原始缺口规则
""",
        encoding="utf-8",
    )
    (child / "scenario.yaml").write_text(
        """
extends: ../base
id: child_sales
name: 子销售场景
patches:
  products:
    - module_id: crm_channel
      set:
        base_score: 55
        target_needs: [sales_channel, overseas_business]
      append_positive_rules:
        - id: bank_high_quality_customer
          source_field: bank_flags.high_quality_customer
          op: "=="
          value: true
          weight: 12
          reason: 银行高质量客户标签提示金融服务匹配度更高
      replace_positive_rules:
        - id: sales_channel_supported
          dimension_id: sales_channel
          evidence_type: supported
          weight: 20
          reason: 子场景提高销售渠道权重
      remove_positive_rules:
        - old_rule
      append_negative_rules:
        - id: missing_overseas_channel
          missing_evidence: 海外渠道
          penalty: 3
          reason: 缺少海外渠道证据
""",
        encoding="utf-8",
    )

    products = load_products_config(child)

    product = products.products[0]
    assert product.base_score == 55
    assert product.target_needs == ["sales_channel", "overseas_business"]
    assert [rule.id for rule in product.positive_rules] == [
        "sales_channel_supported",
        "bank_high_quality_customer",
    ]
    assert product.positive_rules[0].weight == 20
    assert product.negative_rules[-1].id == "missing_overseas_channel"
    scenario = load_scenario(child)
    assert scenario is not None
    resolved_path = write_products_resolved_config(scenario, products, child / "scenario_resolved.json")
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    assert resolved["patches"]["products"][0]["module_id"] == "crm_channel"
    assert resolved["products_effective_count"] == 1
    assert len(resolved["products_effective_hash"]) == 64


def test_scenario_products_patch_rejects_unknown_module(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    (scenario_dir / "scenario.yaml").write_text(
        """
version: "1.0"
id: bad_patch
name: 错误场景
products_config: products.yaml
dimensions_config: analysis_dimensions.yaml
web_search_config: web_search.yaml
web_extract_llm_config: web_extract_llm.yaml
patches:
  products:
    - module_id: missing
      set:
        base_score: 50
""",
        encoding="utf-8",
    )
    (scenario_dir / "products.yaml").write_text(
        """
version: "1.0"
scenario: product_recommendation
products:
  - module_id: crm_channel
    module_name: 客户与渠道管理(CRM)
    priority: 82
    target_needs: [sales_channel]
    match_rule: 原始规则
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown module_id"):
        load_products_config(scenario_dir)


def test_config_loaders_accept_scenario_directory() -> None:
    products = load_products_config(SCENARIO_DIR)
    dimensions = load_dimensions_config(SCENARIO_DIR)
    web_config = load_web_search_config(SCENARIO_DIR)
    extract_config = load_web_extract_llm_config(SCENARIO_DIR)
    scoring_policy = load_scoring_policy(SCENARIO_DIR)
    evidence_policy = load_evidence_policy(SCENARIO_DIR)

    assert products.products
    assert dimensions.dimensions
    assert web_config.cache_root.endswith("data/web/sales_recommendation")
    assert extract_config.prompt_file.endswith("prompts/extract_evidence_system.md")
    assert scoring_policy.dimension_support["supported_score"] == 5
    assert evidence_policy.web_planning.supported_facts_to_skip_web == 3


def test_builtin_bank_marketing_scenario_uses_product_patch(tmp_path: Path) -> None:
    scenario = load_scenario(BANK_SCENARIO_DIR)
    products = load_products_config(BANK_SCENARIO_DIR)

    crm = next(product for product in products.products if product.module_id == "crm_channel")
    assert scenario is not None
    assert scenario.config.id == "bank_marketing"
    assert crm.base_score == 55
    assert {rule.id for rule in crm.positive_rules} >= {
        "bank_high_quality_customer",
        "cross_border_settlement_signal",
    }
    resolved_path = write_products_resolved_config(scenario, products, tmp_path / "scenario_resolved.json")
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    assert resolved["products_effective_count"] == len(products.products)
    assert len(resolved["products_effective_hash"]) == 64


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
    payload = json.loads((Path(result.output_dir) / "result.json").read_text(encoding="utf-8"))
    assert payload["scenario"] == "sales_recommendation"
    assert payload["scenario_name"] == "销售产品推荐"
    assert payload["recommendations"]
    resolved = json.loads((Path(result.output_dir) / "scenario_resolved.json").read_text(encoding="utf-8"))
    assert len(resolved["products_effective_hash"]) == 64
    manifest = json.loads((Path(result.output_dir) / "config_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pipeline"] == "recommender"
    assert manifest["scenario_id"] == "sales_recommendation"
    assert manifest["files"]["products"]["sha256"]
    assert manifest["files"]["prompt:match_system"]["sha256"]
    assert len(manifest["effective_hashes"]["products"]) == 64
    assert len(manifest["effective_hashes"]["dimensions"]) == 64
