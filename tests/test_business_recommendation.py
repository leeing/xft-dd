from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
import pytest

from xft.core.search_models import SearchItem, make_item_id
from xft.pipeline.recommender.business_config_loader import load_business_recommendation_config
from xft.pipeline.recommender.business_evidence_loader import load_business_evidence
from xft.pipeline.recommender.business_evaluator import evaluate_business_recommendation
from xft.pipeline.recommender.business_models import BusinessRecommendationConfig
from xft.pipeline.recommender.business_result_renderer import render_business_result_json
from xft.pipeline.recommender.business_web_evidence import run_business_web_evidence
from xft.pipeline.recommender.business_web_policy import should_search_indicator
from xft.pipeline.recommender.graph import run_recommendation
from xft.web.models import ProviderSearchResponse
from xft.warehouse.prophet_loader import load_prophet_data


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class _FakeBusinessProvider:
    name = "fake_search"

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        item = SearchItem(
            id=make_item_id(url="https://example.com/oem", title="海外代工新闻", snippet="测试公司存在海外代工业务。"),
            title="海外代工新闻",
            url="https://example.com/oem",
            snippet="测试公司存在海外代工业务。",
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


class _NoisyBusinessProvider:
    name = "fake_search"

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        items = [
            SearchItem(
                id=make_item_id(
                    url="https://example.com/other",
                    title="其他公司研发中心",
                    snippet="其他公司拥有研发中心。",
                ),
                title="其他公司研发中心",
                url="https://example.com/other",
                snippet="其他公司拥有研发中心。",
                query=query,
                dimension_id=dimension_id,
                source="minimax",
                rank=0,
                fetched_at=datetime.now(UTC),
            ),
            SearchItem(
                id=make_item_id(
                    url="https://example.com/acme",
                    title="测试公司研发中心",
                    snippet="测试公司设有研发中心。",
                ),
                title="测试公司研发中心",
                url="https://example.com/acme",
                snippet="测试公司设有研发中心。",
                query=query,
                dimension_id=dimension_id,
                source="minimax",
                rank=1,
                fetched_at=datetime.now(UTC),
            ),
        ]
        return ProviderSearchResponse(
            provider=self.name,
            provider_type="minimax",
            query=query,
            dimension_id=dimension_id,
            status="success",
            items=[item.model_dump() for item in items],
        )


def _write_business_web_config(tmp_path: Path) -> Path:
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
                "execution": {"max_results_per_query": 2},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


async def test_business_recommendation_no_llm_generates_result_json_shape() -> None:
    config = load_business_recommendation_config("config/recommend/sales_recommendation")
    assert config is not None

    profile = {
        "company_name": "广东泰琪丰电子有限公司",
        "credit_code": "91440000MA5UW5Y08T",
        "industry": "制造业",
        "industry_mid": "电子设备制造",
        "industry_small": "小家电制造",
        "business_scope": "电子设备、小家电研发、生产、销售、维修服务",
        "labels": ["高新技术企业认定", "科技企业资质4星"],
        "ip_counts": {"patent": 12, "software": 0},
        "recent_recruitment_titles": ["区域销售经理", "渠道业务员", "售后维修工程师"],
    }

    result = await evaluate_business_recommendation(
        config=config,
        company_name="广东泰琪丰电子有限公司",
        profile=profile,
        use_llm=False,
    )
    assert result is not None
    assert result.selected_module is not None
    assert result.selected_module.module_id == "假勤管理"
    assert result.selected_module.attributes_number >= 1
    assert result.selected_module.indicators_number >= 1
    tech_cert = next(item for item in result.indicator_results if item.indicator_id == "科技企业_科技资质认证")
    assert tech_cert.evaluator == "rule"
    assert tech_cert.result in {"matched", "not_matched"}

    payload = render_business_result_json(profile=profile, business_result=result, config=config)

    assert payload["CompanyName"] == "广东泰琪丰电子有限公司"
    assert payload["USCI"] == "91440000MA5UW5Y08T"
    assert payload["Module"] == "假勤管理"
    assert payload["AcceptanceResult"] in {"高", "中高", "低"}
    assert payload["AttributesNumber"] >= 1
    assert payload["LabelResult"]
    assert payload["MarketingPoint"]


def test_business_config_loader_accepts_scenario_bundle() -> None:
    config = load_business_recommendation_config("config/recommend/sales_recommendation")

    assert config is not None
    assert config.modules_dir == "business_modules.d"
    module_ids = {module.module_id for module in config.modules}
    assert module_ids == {
        "假勤管理",
        "个税管理",
        "差旅报销",
        "日常报销",
        "对公报账",
        "进项发票",
        "销项发票",
    }
    attendance = next(module for module in config.modules if module.module_id == "假勤管理")
    assert attendance.labels[0].label_name == "科技属性"
    assert any(ind.evaluator == "llm_web" for label in attendance.labels for ind in label.indicators)


def test_business_config_loader_discovers_module_files(tmp_path: Path) -> None:
    root = tmp_path / "scenario"
    module_dir = root / "business_modules.d"
    module_dir.mkdir(parents=True)
    (root / "business_modules.yaml").write_text(
        yaml.safe_dump(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules_dir": "business_modules.d",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (module_dir / "假勤管理.yaml").write_text(
        yaml.safe_dump(
            {
                "module_id": "attendance",
                "module_name": "假勤管理",
                "labels": [
                    {
                        "label_id": "manufacturing",
                        "label_name": "制造业",
                        "indicators": [
                            {
                                "indicator_id": "industry",
                                "indicator_name": "行业",
                                "evaluator": "rule",
                                "standard": "制造业",
                                "rule": {"source_field": "industry", "op": "contains", "value": "制造"},
                            }
                        ],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    config = load_business_recommendation_config(root)

    assert config is not None
    assert [module.module_id for module in config.modules] == ["attendance"]


def test_web_search_policy_loads_for_llm_and_rule() -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "recommended", "min_matched_labels": 1, "conclusion": "ok"}]},
            "modules": [
                {
                    "module_id": "expense",
                    "module_name": "日常报销",
                    "labels": [
                        {
                            "label_id": "need",
                            "label_name": "存在报销需求",
                            "indicators": [
                                {
                                    "indicator_id": "public_recruiting",
                                    "indicator_name": "公开招聘报销岗位",
                                    "evaluator": "llm",
                                    "standard": "公开信息显示存在报销岗位或费控需求",
                                    "web_search": {
                                        "when": "insufficient",
                                        "effect": "llm_evidence",
                                        "fixed_queries": ["{company_name} 报销 招聘"],
                                        "auto": {"enabled": True, "max_queries": 2, "intent": "查招聘或官网线索"},
                                        "max_results": 3,
                                    },
                                },
                                {
                                    "indicator_id": "industry_rule",
                                    "indicator_name": "行业规则",
                                    "evaluator": "rule",
                                    "standard": "本地行业字段命中",
                                    "rule": {"source_field": "basic.industry", "op": "contains", "value": "制造"},
                                    "web_search": {
                                        "when": "rule_not_matched",
                                        "effect": "possible_on_evidence",
                                        "fixed_queries": ["{company_name} 工厂 生产"],
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )

    llm_indicator = config.modules[0].labels[0].indicators[0]
    rule_indicator = config.modules[0].labels[0].indicators[1]

    assert llm_indicator.web_search is not None
    assert llm_indicator.web_search.when == "insufficient"
    assert llm_indicator.web_search.effect == "llm_evidence"
    assert llm_indicator.web_search.auto.enabled is True
    assert llm_indicator.web_search.auto.max_queries == 2
    assert rule_indicator.web_search is not None
    assert rule_indicator.web_search.effect == "possible_on_evidence"


def test_web_policy_keeps_llm_web_web_first() -> None:
    indicator = (
        BusinessRecommendationConfig.model_validate(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "m",
                        "module_name": "模块",
                        "labels": [
                            {
                                "label_id": "l",
                                "label_name": "标签",
                                "indicators": [
                                    {
                                        "indicator_id": "public_need",
                                        "indicator_name": "公开需求",
                                        "evaluator": "llm_web",
                                        "standard": "公开信息显示需求",
                                        "web_search": {"fixed_queries": ["{company_name} 官网"]},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        .modules[0]
        .labels[0]
        .indicators[0]
    )

    decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result=None)

    assert decision.enabled is True
    assert decision.when == "always"
    assert decision.effect == "llm_evidence"
    assert decision.reason == "llm_web_web_first"


def test_web_policy_triggers_llm_when_insufficient() -> None:
    indicator = (
        BusinessRecommendationConfig.model_validate(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "m",
                        "module_name": "模块",
                        "labels": [
                            {
                                "label_id": "l",
                                "label_name": "标签",
                                "indicators": [
                                    {
                                        "indicator_id": "need",
                                        "indicator_name": "需求",
                                        "evaluator": "llm",
                                        "standard": "判断需求",
                                        "web_search": {
                                            "when": "insufficient",
                                            "fixed_queries": ["{company_name} 报销"],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        .modules[0]
        .labels[0]
        .indicators[0]
    )

    decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result=None)

    assert decision.enabled is True
    assert decision.reason == "local_evidence_insufficient"


def test_web_policy_rule_not_matched_only_triggers_after_rule_miss() -> None:
    indicator = (
        BusinessRecommendationConfig.model_validate(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "m",
                        "module_name": "模块",
                        "labels": [
                            {
                                "label_id": "l",
                                "label_name": "标签",
                                "indicators": [
                                    {
                                        "indicator_id": "industry",
                                        "indicator_name": "行业",
                                        "evaluator": "rule",
                                        "standard": "行业命中",
                                        "rule": {"source_field": "basic.industry", "op": "contains", "value": "制造"},
                                        "web_search": {
                                            "when": "rule_not_matched",
                                            "effect": "possible_on_evidence",
                                            "fixed_queries": ["{company_name} 工厂"],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        .modules[0]
        .labels[0]
        .indicators[0]
    )

    matched_decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result="matched")
    missed_decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result="not_matched")

    assert matched_decision.enabled is False
    assert missed_decision.enabled is True
    assert missed_decision.reason == "rule_not_matched"


async def test_indicator_data_sources_load_and_drive_rule_result(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    company = input_root / "91440000MA5UW5Y08T_广东泰琪丰电子有限公司"
    _write_json(
        company / ".meta.json",
        {"company_name": "广东泰琪丰电子有限公司", "credit_code": "91440000MA5UW5Y08T", "fetchers": {}},
    )
    _write_json(
        company / "info.json",
        {
            "data": {
                "info": {
                    "info": {
                        "name": "广东泰琪丰电子有限公司",
                        "unifiedSocialCreditCode": "91440000MA5UW5Y08T",
                        "cate1": "制造业",
                        "businessScope": "电子设备生产销售",
                    }
                }
            }
        },
    )
    _write_json(company / "query_company.json", {"data": {"employeeNum": 300, "idtCtgNm": "制造业"}})
    _write_json(
        company / "recruit_message.json",
        {"data": {"list": [{"title": "区域销售经理"}, {"title": "渠道维护业务员"}]}},
    )
    db = tmp_path / "warehouse.duckdb"
    load_prophet_data(input_root=input_root, output_db=db)
    profile = {
        "company_name": "广东泰琪丰电子有限公司",
        "credit_code": "91440000MA5UW5Y08T",
        "industry": "制造业",
    }
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "daily_reimbursement",
                    "module_name": "日常报销",
                    "acceptance_policy": {
                        "levels": [
                            {
                                "result": "高",
                                "min_matched_labels": 1,
                                "conclusion": "模块级高 {attributes_number}/{indicators_number}",
                            },
                            {"result": "低", "min_matched_labels": 0, "conclusion": "模块级低"},
                        ]
                    },
                    "labels": [
                        {
                            "label_id": "sales",
                            "label_name": "销售属性",
                            "indicators": [
                                {
                                    "indicator_id": "channel_sales",
                                    "indicator_name": "渠道销售岗位",
                                    "evaluator": "rule",
                                    "standard": "招聘标题包含销售或渠道",
                                    "data_sources": [
                                        {
                                            "type": "table",
                                            "table": "recruitments",
                                            "field": "title",
                                            "op": "text_contains",
                                            "keywords": ["销售", "渠道"],
                                            "min_matches": 1,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    evidence = load_business_evidence(config=config, warehouse_db=str(db), profile=profile)

    result = await evaluate_business_recommendation(
        config=config,
        company_name="广东泰琪丰电子有限公司",
        profile=profile,
        business_evidence=evidence,
        use_llm=False,
    )

    assert result is not None
    assert result.selected_module is not None
    assert result.selected_module.acceptance_result == "高"
    indicator = result.indicator_results[0]
    assert indicator.result == "matched"
    assert indicator.evidence_details
    assert "recruitments.title 命中" in indicator.evidence[0]


async def test_llm_web_fixed_queries_are_rendered_without_llm() -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "attendance",
                    "module_name": "假勤管理",
                    "labels": [
                        {
                            "label_id": "export",
                            "label_name": "出口海外",
                            "indicators": [
                                {
                                    "indicator_id": "overseas_oem",
                                    "indicator_name": "海外代工",
                                    "evaluator": "llm_web",
                                    "standard": "存在海外代工业务线索",
                                    "prompt": "判断是否存在海外代工。",
                                    "web_search": {
                                        "fixed_queries": ["{company_name} 海外代工", "{company_name} 出口营收"],
                                        "auto": False,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = await evaluate_business_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        use_llm=False,
    )

    assert result is not None
    trace = result.indicator_results[0].web_search_trace
    assert [item["query"] for item in trace] == ["测试公司 海外代工", "测试公司 出口营收"]


async def test_business_web_evidence_executes_fixed_queries(tmp_path: Path) -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "attendance",
                    "module_name": "假勤管理",
                    "labels": [
                        {
                            "label_id": "export",
                            "label_name": "出口海外",
                            "indicators": [
                                {
                                    "indicator_id": "overseas_oem",
                                    "indicator_name": "海外代工",
                                    "evaluator": "llm_web",
                                    "standard": "存在海外代工业务线索",
                                    "prompt": "判断是否存在海外代工。",
                                    "web_search": {
                                        "fixed_queries": ["{company_name} 海外代工"],
                                        "auto": True,
                                        "max_auto_rounds": 1,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = await run_business_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司", "credit_code": "91440000MA5UW5Y08T"},
        web_config_path=str(_write_business_web_config(tmp_path)),
        output_dir=tmp_path / "run",
        provider_factory=lambda _name, _config: _FakeBusinessProvider(),
        query_planner=lambda **_: [],
    )

    key = "attendance.export.overseas_oem"
    assert result.queries == 1
    assert result.results == 1
    assert result.evidence[key][0]["source_type"] == "web"
    assert "海外代工新闻" in result.evidence[key][0]["evidence"]
    assert any(item.get("auto") is True and item.get("status") == "skipped" for item in result.trace)
    assert (tmp_path / "run" / "business_web_queries.jsonl").exists()
    assert (tmp_path / "run" / "business_web_results.jsonl").exists()
    assert (tmp_path / "run" / "business_web_trace.json").exists()


async def test_business_web_evidence_runs_llm_fixed_queries(tmp_path: Path) -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "expense",
                    "module_name": "日常报销",
                    "labels": [
                        {
                            "label_id": "need",
                            "label_name": "存在报销需求",
                            "indicators": [
                                {
                                    "indicator_id": "recruiting",
                                    "indicator_name": "招聘报销岗位",
                                    "evaluator": "llm",
                                    "standard": "公开招聘存在报销岗位",
                                    "web_search": {
                                        "when": "insufficient",
                                        "fixed_queries": ["{company_name} 报销 招聘"],
                                        "max_results": 2,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = await run_business_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        web_config_path=str(_write_business_web_config(tmp_path)),
        output_dir=tmp_path / "out",
        provider_factory=lambda _name, _config: _FakeBusinessProvider(),
        refresh=True,
    )

    key = "expense.need.recruiting"
    assert result.queries == 1
    assert key in result.evidence
    assert result.trace[0]["trigger_reason"] == "local_evidence_insufficient"


async def test_business_web_evidence_runs_auto_queries_after_fixed_queries(tmp_path: Path) -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "tax",
                    "module_name": "个税管理",
                    "labels": [
                        {
                            "label_id": "payroll",
                            "label_name": "薪酬个税需求",
                            "indicators": [
                                {
                                    "indicator_id": "public_payroll",
                                    "indicator_name": "公开薪酬个税线索",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息显示薪酬或个税管理需求",
                                    "web_search": {
                                        "fixed_queries": ["{company_name} 薪酬"],
                                        "auto": {
                                            "enabled": True,
                                            "max_queries": 1,
                                            "intent": "查个税或薪酬管理公开线索",
                                        },
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    async def fake_query_planner(**kwargs: object) -> list[str]:
        return ["测试公司 个税 管理 招聘"]

    result = await run_business_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        web_config_path=str(_write_business_web_config(tmp_path)),
        output_dir=tmp_path / "out",
        provider_factory=lambda _name, _config: _FakeBusinessProvider(),
        query_planner=fake_query_planner,
        refresh=True,
    )

    assert result.queries == 2
    assert any(row.get("auto") is True and row.get("query") == "测试公司 个税 管理 招聘" for row in result.trace)


async def test_plan_auto_queries_with_llm_returns_bounded_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    from xft.pipeline.recommender.business_web_evidence import _plan_auto_queries_with_llm

    class FakeMessage:
        content = '{"queries": ["测试公司 差旅 招聘", "测试公司 费控 系统", "多余 查询"]}'

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        async def create(self, **_kwargs: object) -> object:
            return type("Resp", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    def fake_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr("xft.pipeline.recommender.business_web_evidence.get_ai_client", fake_client)
    monkeypatch.setattr("xft.pipeline.recommender.business_web_evidence.settings.llm_api_key", "test")

    indicator = (
        BusinessRecommendationConfig.model_validate(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "m",
                        "module_name": "模块",
                        "labels": [
                            {
                                "label_id": "l",
                                "label_name": "标签",
                                "indicators": [
                                    {
                                        "indicator_id": "travel",
                                        "indicator_name": "差旅线索",
                                        "evaluator": "llm",
                                        "standard": "判断差旅需求",
                                        "web_search": {
                                            "auto": {"enabled": True, "max_queries": 2, "intent": "查差旅公开线索"}
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        .modules[0]
        .labels[0]
        .indicators[0]
    )

    queries = await _plan_auto_queries_with_llm(
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        module_id="travel",
        module_name="差旅报销",
        label_id="need",
        label_name="存在需求",
        indicator=indicator,
        max_queries=2,
        intent="查差旅公开线索",
    )

    assert queries == ["测试公司 差旅 招聘", "测试公司 费控 系统"]


async def test_plan_auto_queries_coerces_single_string_and_includes_indicator_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xft.pipeline.recommender.business_web_evidence import _plan_auto_queries_with_llm

    seen_kwargs: dict[str, object] = {}

    class FakeMessage:
        content = '{"queries": "测试公司"}'

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        async def create(self, **kwargs: object) -> object:
            seen_kwargs.update(kwargs)
            return type("Resp", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("xft.pipeline.recommender.business_web_evidence.get_ai_client", FakeClient)
    monkeypatch.setattr("xft.pipeline.recommender.business_web_evidence.settings.llm_api_key", "test")

    indicator = (
        BusinessRecommendationConfig.model_validate(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "m",
                        "module_name": "模块",
                        "labels": [
                            {
                                "label_id": "l",
                                "label_name": "标签",
                                "indicators": [
                                    {
                                        "indicator_id": "rd",
                                        "indicator_name": "研发中心",
                                        "evaluator": "llm",
                                        "standard": "公开信息显示有研发中心",
                                        "web_search": {
                                            "auto": {"enabled": True, "max_queries": 1, "intent": "查研发中心"}
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        .modules[0]
        .labels[0]
        .indicators[0]
    )

    queries = await _plan_auto_queries_with_llm(
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        module_id="m",
        module_name="模块",
        label_id="l",
        label_name="标签",
        indicator=indicator,
        max_queries=1,
        intent="查研发中心",
    )

    assert queries == ["测试公司 研发中心"]
    assert seen_kwargs["response_format"] == {"type": "json_object"}


async def test_business_web_evidence_filters_results_for_other_companies(tmp_path: Path) -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "m",
                    "module_name": "模块",
                    "labels": [
                        {
                            "label_id": "l",
                            "label_name": "标签",
                            "indicators": [
                                {
                                    "indicator_id": "rd",
                                    "indicator_name": "研发中心",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息显示有研发中心",
                                    "web_search": {"fixed_queries": ["{company_name} 官网"]},
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = await run_business_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司", "credit_code": "91440000MA5UW5Y08T"},
        web_config_path=str(_write_business_web_config(tmp_path)),
        output_dir=tmp_path / "out",
        provider_factory=lambda _name, _config: _NoisyBusinessProvider(),
        refresh=True,
    )

    key = "m.l.rd"
    assert result.results == 1
    assert len(result.evidence[key]) == 1
    assert result.evidence[key][0]["title"] == "测试公司研发中心"
    assert result.trace[0]["filtered_result_count"] == 1


async def test_business_web_evidence_feeds_indicator_result() -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "attendance",
                    "module_name": "假勤管理",
                    "labels": [
                        {
                            "label_id": "export",
                            "label_name": "出口海外",
                            "indicators": [
                                {
                                    "indicator_id": "overseas_oem",
                                    "indicator_name": "海外代工",
                                    "evaluator": "llm_web",
                                    "standard": "存在海外代工业务线索",
                                    "prompt": "判断是否存在海外代工。",
                                    "web_search": {"fixed_queries": ["{company_name} 海外代工"]},
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    key = "attendance.export.overseas_oem"
    evidence = {
        key: [
            {
                "source_type": "web",
                "source": "fake:https://example.com/oem",
                "matched": True,
                "evidence": "海外代工新闻：测试公司存在海外代工业务。",
                "url": "https://example.com/oem",
            }
        ]
    }
    trace = [{"indicator_key": key, "query": "测试公司 海外代工", "result_count": 1}]

    result = await evaluate_business_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        business_evidence=evidence,
        business_web_trace=trace,
        use_llm=False,
    )

    assert result is not None
    indicator = result.indicator_results[0]
    assert indicator.result == "matched"
    assert indicator.evidence_details[0]["source_type"] == "web"
    assert indicator.web_search_trace[0]["result_count"] == 1


async def test_rule_web_evidence_can_only_raise_to_possible() -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "travel",
                    "module_name": "差旅报销",
                    "labels": [
                        {
                            "label_id": "travel_need",
                            "label_name": "存在差旅需求",
                            "indicators": [
                                {
                                    "indicator_id": "industry",
                                    "indicator_name": "本地行业未命中但公开信息有差旅",
                                    "evaluator": "rule",
                                    "standard": "行业或公开信息显示存在差旅需求",
                                    "rule": {"source_field": "basic.industry", "op": "contains", "value": "制造"},
                                    "web_search": {
                                        "when": "rule_not_matched",
                                        "effect": "possible_on_evidence",
                                        "fixed_queries": ["{company_name} 差旅"],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    business_evidence = {
        "travel.travel_need.industry": [
            {
                "source_type": "web",
                "source": "search:https://example.test",
                "matched": True,
                "evidence": "测试公司官网显示全国多地分支机构和差旅安排。",
            }
        ]
    }

    result = await evaluate_business_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司", "basic": {"industry": "软件服务"}},
        business_evidence=business_evidence,
        use_llm=False,
    )

    assert result is not None
    indicator = result.indicator_results[0]
    assert indicator.result == "possible"
    assert indicator.confidence == "中"
    assert "测试公司官网显示全国多地分支机构和差旅安排。" in indicator.evidence


async def test_llm_validation_error_falls_back_to_unknown_not_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {
                "levels": [
                    {"result": "高", "min_matched_labels": 1, "conclusion": "高"},
                    {"result": "低", "min_matched_labels": 0, "conclusion": "低"},
                ]
            },
            "modules": [
                {
                    "module_id": "attendance",
                    "module_name": "假勤管理",
                    "labels": [
                        {
                            "label_id": "branches",
                            "label_name": "多分支机构",
                            "indicators": [
                                {
                                    "indicator_id": "branch",
                                    "indicator_name": "分支机构",
                                    "evaluator": "llm_web",
                                    "standard": "工商信息显示有分支机构",
                                    "prompt": "判断是否有分支机构。",
                                    "evidence_hints": ["分支机构"],
                                    "web_search": {"fixed_queries": ["{company_name} 分支机构"]},
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    class FakeMessage:
        content = (
            '{"result": "yes", "confidence": "中", "current_status": "存在分支机构", "evidence": {"bad": "shape"}}'
        )

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        async def create(self, **_kwargs: object) -> object:
            return type("Resp", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("xft.pipeline.recommender.business_evaluator.get_ai_client", FakeClient)
    monkeypatch.setattr("xft.pipeline.recommender.business_evaluator.settings.llm_api_key", "test")

    result = await evaluate_business_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司", "branch_count": 0, "notes": "分支机构字段仅为指标名"},
        business_evidence={
            "attendance.branches.branch": [
                {"source_type": "web", "matched": True, "evidence": "其他公司存在分支机构。"}
            ]
        },
        use_llm=True,
    )

    assert result is not None
    indicator = result.indicator_results[0]
    assert indicator.result == "unknown"
    assert indicator.score == 0
    assert result.selected_module is not None
    assert result.selected_module.acceptance_result == "低"


async def test_low_confidence_web_only_module_is_capped_below_high() -> None:
    config = BusinessRecommendationConfig.model_validate(
        {
            "acceptance_policy": {
                "levels": [
                    {"result": "高", "min_matched_labels": 2, "conclusion": "高"},
                    {"result": "中高", "min_matched_labels": 1, "conclusion": "中高"},
                    {"result": "低", "min_matched_labels": 0, "conclusion": "低"},
                ]
            },
            "modules": [
                {
                    "module_id": "travel",
                    "module_name": "差旅报销",
                    "labels": [
                        {
                            "label_id": "a",
                            "label_name": "属性A",
                            "indicators": [
                                {
                                    "indicator_id": "ia",
                                    "indicator_name": "指标A",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息命中",
                                    "prompt": "判断。",
                                    "web_search": {"fixed_queries": ["{company_name} A"]},
                                }
                            ],
                        },
                        {
                            "label_id": "b",
                            "label_name": "属性B",
                            "indicators": [
                                {
                                    "indicator_id": "ib",
                                    "indicator_name": "指标B",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息命中",
                                    "prompt": "判断。",
                                    "web_search": {"fixed_queries": ["{company_name} B"]},
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )
    business_evidence = {
        "travel.a.ia": [{"source_type": "web", "matched": True, "evidence": "测试公司疑似存在A。"}],
        "travel.b.ib": [{"source_type": "web", "matched": True, "evidence": "测试公司疑似存在B。"}],
    }

    result = await evaluate_business_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        business_evidence=business_evidence,
        use_llm=False,
    )

    assert result is not None
    assert result.selected_module is not None
    assert result.selected_module.attributes_number == 2
    assert result.selected_module.acceptance_result == "中高"


async def test_run_recommendation_business_first_ignores_dimension_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "data"
    company = input_root / "91440000MA5UW5Y08T_广东泰琪丰电子有限公司"
    _write_json(
        company / ".meta.json",
        {"company_name": "广东泰琪丰电子有限公司", "credit_code": "91440000MA5UW5Y08T", "fetchers": {}},
    )
    _write_json(
        company / "info.json",
        {
            "data": {
                "info": {
                    "info": {
                        "name": "广东泰琪丰电子有限公司",
                        "unifiedSocialCreditCode": "91440000MA5UW5Y08T",
                        "cate1": "制造业",
                        "businessScope": "电子设备生产销售",
                    }
                }
            }
        },
    )
    _write_json(company / "query_company.json", {"data": {"employeeNum": 300, "idtCtgNm": "制造业"}})
    db = tmp_path / "warehouse.duckdb"
    load_prophet_data(input_root=input_root, output_db=db)
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    (scenario_dir / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "extends": str(Path("config/recommend/sales_recommendation/scenario.yaml").resolve()),
                "id": "test",
                "name": "test",
                "business_modules_config": "business_modules.yaml",
                "output_dir": str(tmp_path / "runs"),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (scenario_dir / "business_modules.yaml").write_text(
        yaml.safe_dump(
            {
                "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
                "modules": [
                    {
                        "module_id": "attendance",
                        "module_name": "假勤管理",
                        "labels": [
                            {
                                "label_id": "manufacturing",
                                "label_name": "制造业",
                                "indicators": [
                                    {
                                        "indicator_id": "industry",
                                        "indicator_name": "行业",
                                        "evaluator": "rule",
                                        "standard": "制造业",
                                        "rule": {"source_field": "industry", "op": "contains", "value": "制造"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = await run_recommendation(
        company_name="广东泰琪丰电子有限公司",
        warehouse_db=str(db),
        scenario_path=str(scenario_dir),
        output_dir=str(tmp_path / "runs"),
        run_id="business-first",
        use_llm=False,
    )

    output_dir = Path(result.output_dir)
    assert not (output_dir / "dimension_analysis.json").exists()
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "业务推荐结果" in report
    assert "维度分析摘要" not in report
