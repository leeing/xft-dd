# Business Web Search Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `web_search` an indicator-level evidence policy available to `rule`, `llm`, `hybrid`, and `llm_web`, while keeping Web as a controlled business recommendation capability.

**Architecture:** Keep `llm_web` as the explicit "Web-first LLM" evaluator for indicators whose evidence is unlikely to exist locally. Add `web_search.when` and `web_search.effect` so other evaluators can use Web only when local evidence is insufficient. Fixed queries run first; optional LLM-generated queries run only when configured and only within small budgets.

**Tech Stack:** Python 3.12, Pydantic models, async Web providers under `src/xft/web`, recommendation pipeline under `src/xft/pipeline/recommender`, pytest, ruff, mypy.

---

## Product Decision

Keep `llm_web`.

Rationale:

- Some business indicators are inherently external: company官网业务线、招聘岗位、新闻动态、公开招投标、门店/分支、产品/服务页面等. These are not expected to exist in local structured databases.
- Removing `llm_web` would force us to express "Web-first" as `llm + web_search.when: always`, which works mechanically but hides intent.
- Keeping `llm_web` makes configuration easier to read: this indicator is designed to search Web first, then let LLM judge.
- Other evaluators can still cover the same behavior technically:
  - `llm + web_search.when: always` can behave like `llm_web`.
  - `hybrid + web_search.when: insufficient` can use Web only after rule/local evidence is weak.
  - `rule + web_search.effect: possible_on_evidence` can surface Web clues without pretending the deterministic rule matched.
- Therefore `llm_web` should be retained as a first-class evaluator, not deleted, but its implementation should reuse the same generic Web policy engine as the other evaluators.

Evaluator semantics after this change:

| Evaluator | Local First | Web Policy | Web Can Change Final Result |
| --- | --- | --- | --- |
| `rule` | Yes | Optional, usually `when: rule_not_matched` or `insufficient` | Only to `possible` when `effect: possible_on_evidence` |
| `llm` | Yes | Optional, usually `when: insufficient` | Yes, through LLM judgment on combined evidence |
| `hybrid` | Yes | Optional, usually `when: insufficient` | Yes, through existing hybrid merge policy |
| `llm_web` | No, Web-first | Required, default `when: always` | Yes, through LLM judgment on Web evidence |

---

## File Structure

Modify:

- `src/xft/pipeline/recommender/business_models.py`
  - Add typed Web policy fields: `when`, `effect`, structured `auto`.
  - Keep backwards compatibility for existing `auto: bool`.
  - Relax validation so `web_search` is allowed on all evaluators.
  - Keep `llm_web` requiring `web_search`.

- `src/xft/pipeline/recommender/business_web_policy.py`
  - New small module.
  - Owns Web trigger decisions:
    - whether an indicator should be searched
    - why it should be searched
    - how `rule` may consume Web evidence
    - how to preserve `llm_web` as Web-first

- `src/xft/pipeline/recommender/web_evidence.py`
  - Stop filtering only `indicator.evaluator == "llm_web"`.
  - Use `business_web_policy` to decide which indicators to search.
  - Execute fixed queries for all eligible indicators.
  - Implement optional LLM-generated query planning for configured indicators.
  - Persist `auto` and `trigger_reason` in query rows and trace.

- `src/xft/pipeline/recommender/business_evaluator.py`
  - Ensure Web evidence is treated as `indicator_evidence` for `llm`, `hybrid`, and `llm_web`.
  - For `rule`, allow Web evidence to add trace/evidence and optionally raise final result to `possible`, never `matched`.
  - Update planned trace rendering to include `when`, `effect`, and auto query intent.

- `src/xft/pipeline/recommender/nodes/web_evidence_node.py`
  - Update copy from "llm_web fixed queries" to generic indicator Web evidence.
  - Display query count and skipped reason more accurately.

- `src/xft/cli/recommend.py`
  - Update help text for `--with-web`.

- `src/xft/pipeline/recommender/nodes/save_node.py`
  - Update rationale text so it no longer implies only `llm_web` can use Web.

- `README.md`
  - Document evaluator semantics, `web_search.when`, `web_search.effect`, fixed queries, auto queries, and when to choose `llm_web`.

- `docs/ARCHITECTURE.md`
  - Update graph and table from "`llm_web` only" to "indicator-level Web policy".

- `docs/SCORING.md`
  - Explain how Web affects each evaluator and how scoring remains bounded.

- `docs/SMOKE.md`
  - Add smoke commands for fixed Web, Web-first, and supplemental Web.

- `docs/NEXT.md`
  - Replace old `llm_web fixed_queries` tuning notes with policy-based tuning notes.

- `docs/TECH_DEBT.md`
  - Remove or update "auto query generation is pending" after implementation.

Tests:

- `tests/test_recommendation.py`
  - Main unit coverage for model validation, Web policy, fixed query execution, auto query planning, evaluator behavior.

- `tests/test_scenario_bundle.py`
  - Validate scenario config still loads with existing module files.

- `tests/test_xft_cli.py`
  - Check CLI help/args still expose `--with-web`.

---

## Config Shape

Target YAML:

```yaml
evaluator: llm
web_search:
  when: insufficient
  effect: llm_evidence
  fixed_queries:
    - "{company_name} 差旅 报销 制度"
  auto:
    enabled: true
    max_queries: 2
    intent: "判断企业是否有差旅、商旅、报销、费控管理需求"
  max_results: 5
```

Allowed `when` values:

- `always`: search whenever `--with-web` is enabled. Default for `llm_web`.
- `insufficient`: search when local indicator evidence is missing or weak. Recommended for `llm` and `hybrid`.
- `rule_not_matched`: search only when deterministic rule did not match. Useful for `rule`.
- `never`: keep config documented but disabled.

Allowed `effect` values:

- `llm_evidence`: Web evidence is added to indicator evidence and judged by LLM. Default for `llm`, `hybrid`, and `llm_web`.
- `evidence_only`: Web evidence appears in trace/evidence details but does not alter deterministic rule result. Safe default for `rule`.
- `possible_on_evidence`: for `rule`, Web evidence can lift `not_matched` or `unknown` to `possible`, never to `matched`.

Backwards compatibility:

```yaml
web_search:
  fixed_queries:
    - "{company_name} 官网"
  auto: true
  max_auto_rounds: 1
```

Should load as:

```yaml
web_search:
  when: always for llm_web, insufficient for others unless explicitly set
  effect: llm_evidence for llm/hybrid/llm_web, evidence_only for rule unless explicitly set
  auto:
    enabled: true
    max_queries: 1
```

---

## Tasks

### Task 1: Add Web Policy Model Fields

**Files:**

- Modify: `src/xft/pipeline/recommender/business_models.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write failing tests for new config fields**

Add tests near existing business config/model tests:

```python
def test_web_search_policy_loads_for_llm_and_rule() -> None:
    config = RecommendationConfig.model_validate(
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
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_search_policy_loads_for_llm_and_rule -q
```

Expected: fail because `when`, `effect`, and structured `auto` are not modeled yet.

- [ ] **Step 3: Implement model fields**

In `src/xft/pipeline/recommender/business_models.py`, add:

```python
WebSearchWhen = Literal["always", "insufficient", "rule_not_matched", "never"]
WebSearchEffect = Literal["llm_evidence", "evidence_only", "possible_on_evidence"]


class WebAutoConfig(BaseModel):
    """LLM-generated query policy for an indicator."""

    enabled: bool = False
    max_queries: int = Field(default=0, ge=0, le=5)
    intent: str = ""
```

Then replace `WebSearchConfig` with:

```python
class WebSearchConfig(BaseModel):
    """Indicator-level web search policy."""

    fixed_queries: list[str] = Field(default_factory=list)
    when: WebSearchWhen | None = None
    effect: WebSearchEffect | None = None
    auto: WebAutoConfig | bool = Field(default_factory=WebAutoConfig)
    max_auto_rounds: int = Field(default=0, ge=0, le=3)
    max_results: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def normalize_auto(self) -> WebSearchConfig:
        if isinstance(self.auto, bool):
            self.auto = WebAutoConfig(enabled=self.auto, max_queries=self.max_auto_rounds)
        elif self.auto.enabled and self.auto.max_queries == 0:
            self.auto.max_queries = self.max_auto_rounds or 1
        return self
```

Keep `llm_web` requiring `web_search`.

- [ ] **Step 4: Run model test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_search_policy_loads_for_llm_and_rule -q
```

Expected: pass.

### Task 2: Add Web Policy Decision Module

**Files:**

- Create: `src/xft/pipeline/recommender/business_web_policy.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write failing tests for trigger decisions**

Add:

```python
def test_web_policy_keeps_llm_web_web_first() -> None:
    indicator = IndicatorConfig.model_validate(
        {
            "indicator_id": "public_need",
            "indicator_name": "公开需求",
            "evaluator": "llm_web",
            "standard": "公开信息显示需求",
            "web_search": {"fixed_queries": ["{company_name} 官网"]},
        }
    )

    decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result=None)

    assert decision.enabled is True
    assert decision.when == "always"
    assert decision.effect == "llm_evidence"
    assert decision.reason == "llm_web_web_first"


def test_web_policy_triggers_llm_when_insufficient() -> None:
    indicator = IndicatorConfig.model_validate(
        {
            "indicator_id": "need",
            "indicator_name": "需求",
            "evaluator": "llm",
            "standard": "判断需求",
            "web_search": {"when": "insufficient", "fixed_queries": ["{company_name} 报销"]},
        }
    )

    decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result=None)

    assert decision.enabled is True
    assert decision.reason == "local_evidence_insufficient"


def test_web_policy_rule_not_matched_only_triggers_after_rule_miss() -> None:
    indicator = IndicatorConfig.model_validate(
        {
            "indicator_id": "industry",
            "indicator_name": "行业",
            "evaluator": "rule",
            "standard": "行业命中",
            "rule": {"source_field": "basic.industry", "op": "contains", "value": "制造"},
            "web_search": {"when": "rule_not_matched", "effect": "possible_on_evidence", "fixed_queries": ["{company_name} 工厂"]},
        }
    )

    matched_decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result="matched")
    missed_decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result="not_matched")

    assert matched_decision.enabled is False
    assert missed_decision.enabled is True
    assert missed_decision.reason == "rule_not_matched"
```

Import the new helper:

```python
from xft.pipeline.recommender.web_policy import should_search_indicator
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_policy_keeps_llm_web_web_first tests/test_recommendation.py::test_web_policy_triggers_llm_when_insufficient tests/test_recommendation.py::test_web_policy_rule_not_matched_only_triggers_after_rule_miss -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement `business_web_policy.py`**

Create:

```python
"""Indicator-level Web search policy decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xft.pipeline.recommender.models import (
    IndicatorConfig,
    Result,
    WebSearchEffect,
    WebSearchWhen,
)


@dataclass(frozen=True)
class WebSearchDecision:
    enabled: bool
    when: WebSearchWhen
    effect: WebSearchEffect
    reason: str


def should_search_indicator(
    *,
    indicator: IndicatorConfig,
    local_evidence: list[dict[str, object]],
    rule_result: Result | None,
) -> WebSearchDecision:
    web = indicator.web_search
    if web is None:
        return WebSearchDecision(False, "never", _default_effect(indicator), "no_web_search_config")

    when = web.when or _default_when(indicator)
    effect = web.effect or _default_effect(indicator)
    if when == "never":
        return WebSearchDecision(False, when, effect, "web_search_disabled")
    if when == "always":
        return WebSearchDecision(True, when, effect, "llm_web_web_first" if indicator.evaluator == "llm_web" else "always")
    if when == "rule_not_matched":
        if rule_result in ("not_matched", "unknown", None):
            return WebSearchDecision(True, when, effect, "rule_not_matched")
        return WebSearchDecision(False, when, effect, "rule_already_matched")
    if _is_insufficient(local_evidence):
        return WebSearchDecision(True, when, effect, "local_evidence_insufficient")
    return WebSearchDecision(False, when, effect, "local_evidence_sufficient")


def _default_when(indicator: IndicatorConfig) -> WebSearchWhen:
    if indicator.evaluator == "llm_web":
        return "always"
    if indicator.evaluator == "rule":
        return "rule_not_matched"
    return "insufficient"


def _default_effect(indicator: IndicatorConfig) -> WebSearchEffect:
    if indicator.evaluator == "rule":
        return "evidence_only"
    return "llm_evidence"


def _is_insufficient(local_evidence: list[dict[str, object]]) -> bool:
    if not local_evidence:
        return True
    return not any(bool(item.get("matched")) for item in local_evidence)
```

- [ ] **Step 4: Run policy tests**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_policy_keeps_llm_web_web_first tests/test_recommendation.py::test_web_policy_triggers_llm_when_insufficient tests/test_recommendation.py::test_web_policy_rule_not_matched_only_triggers_after_rule_miss -q
```

Expected: pass.

### Task 3: Run Web Evidence for All Eligible Evaluators

**Files:**

- Modify: `src/xft/pipeline/recommender/web_evidence.py`
- Modify: `src/xft/pipeline/recommender/nodes/web_evidence_node.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write failing test for `llm` fixed query execution**

Add a test mirroring the existing `llm_web` fixed query test:

```python
@pytest.mark.asyncio
async def test_web_evidence_runs_llm_fixed_queries(tmp_path: Path) -> None:
    web_config_path = _write_web_config(tmp_path)
    config = RecommendationConfig.model_validate(
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

    result = await run_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        web_config_path=str(web_config_path),
        output_dir=tmp_path / "out",
        provider_factory=_fake_provider_factory,
        refresh=True,
    )

    key = "expense.need.recruiting"
    assert result.queries == 1
    assert key in result.evidence
    assert result.trace[0]["trigger_reason"] == "local_evidence_insufficient"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_runs_llm_fixed_queries -q
```

Expected: fail because `web_evidence.py` filters out non-`llm_web`.

- [ ] **Step 3: Update Web evidence loop**

In `web_evidence.py`:

- Import:

```python
from xft.pipeline.recommender.evidence_loader import indicator_key
from xft.pipeline.recommender.web_policy import should_search_indicator
```

- Replace:

```python
if indicator.evaluator != "llm_web" or indicator.web_search is None:
    continue
```

With:

```python
if indicator.web_search is None:
    continue
key = indicator_key(module, label.label_id, indicator)
local_evidence = []
decision = should_search_indicator(indicator=indicator, local_evidence=local_evidence, rule_result=None)
if not decision.enabled:
    trace.append(_skip_trace(module, label, indicator, key, decision))
    continue
```

- Pass `decision.reason` and `decision.effect` into query rows and trace rows.

Add:

```python
def _skip_trace(module, label, indicator, key, decision) -> dict[str, Any]:
    return {
        "indicator_key": key,
        "module_id": module.module_id,
        "label_id": label.label_id,
        "indicator_id": indicator.indicator_id,
        "status": "skipped",
        "trigger_reason": decision.reason,
        "when": decision.when,
        "effect": decision.effect,
    }
```

- [ ] **Step 4: Update node display text**

In `web_evidence_node.py`, change the docstring and skip text from:

```python
"""Run fixed Web queries declared on llm_web indicators."""
```

To:

```python
"""Run indicator-level Web queries declared by business Web policies."""
```

Change display copy from:

```python
display.skip("业务 Web 证据: 无 llm_web fixed queries 或无可用 provider")
```

To:

```python
display.skip("业务 Web 证据: 无可执行的指标级查询或无可用 provider")
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_runs_llm_fixed_queries tests/test_recommendation.py::test_llm_web_fixed_queries_are_rendered_without_llm -q
```

Expected: pass.

### Task 4: Add Rule Web Evidence Effects

**Files:**

- Modify: `src/xft/pipeline/recommender/business_evaluator.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write failing test for `rule` possible-on-evidence**

Add:

```python
@pytest.mark.asyncio
async def test_rule_web_evidence_can_only_raise_to_possible() -> None:
    config = RecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "recommended", "min_matched_labels": 1, "conclusion": "ok"}]},
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
    evidence = {
        "travel.travel_need.industry": [
            {
                "source_type": "web",
                "source": "search:https://example.test",
                "matched": True,
                "evidence": "测试公司官网显示全国多地分支机构和差旅安排。",
            }
        ]
    }

    result = await evaluate_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司", "basic": {"industry": "软件服务"}},
        evidence=evidence,
        use_llm=False,
    )

    indicator = result.indicator_results[0]
    assert indicator.result == "possible"
    assert indicator.confidence == "中"
    assert "测试公司官网显示全国多地分支机构和差旅安排。" in indicator.evidence
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_rule_web_evidence_can_only_raise_to_possible -q
```

Expected: fail because rule currently treats any matched `indicator_evidence` as matched when `data_sources` are present, or ignores Web effect for plain rules.

- [ ] **Step 3: Implement rule Web effect**

In `_evaluate_rule_indicator`, after computing the deterministic rule result for `indicator.rule`, add:

```python
    web_evidence = [
        item for item in indicator_evidence
        if item.get("source_type") == "web" and item.get("matched") and item.get("evidence")
    ]
    if (
        rule_result != "matched"
        and indicator.web_search is not None
        and (indicator.web_search.effect or "evidence_only") == "possible_on_evidence"
        and web_evidence
    ):
        evidence = [str(item.get("evidence")) for item in web_evidence[:3]]
        return _indicator_result(
            config=config,
            module=module,
            label=label,
            indicator=indicator,
            result="possible",
            confidence="中",
            current_status="；".join(evidence[:2]),
            evidence=evidence,
            evidence_details=indicator_evidence,
            web_search_trace=_render_web_search_trace(
                company_name=str(profile.get("company_name") or ""),
                indicator=indicator,
            ),
        )
```

Ensure this branch never returns `matched`.

- [ ] **Step 4: Run rule test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_rule_web_evidence_can_only_raise_to_possible -q
```

Expected: pass.

### Task 5: Implement Auto Query Planning

**Files:**

- Modify: `src/xft/pipeline/recommender/web_evidence.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write failing test with fake planner**

Add:

```python
@pytest.mark.asyncio
async def test_web_evidence_runs_auto_queries_after_fixed_queries(tmp_path: Path) -> None:
    web_config_path = _write_web_config(tmp_path)
    config = RecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "recommended", "min_matched_labels": 1, "conclusion": "ok"}]},
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
                                        "auto": {"enabled": True, "max_queries": 1, "intent": "查个税或薪酬管理公开线索"},
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

    result = await run_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        web_config_path=str(web_config_path),
        output_dir=tmp_path / "out",
        provider_factory=_fake_provider_factory,
        query_planner=fake_query_planner,
        refresh=True,
    )

    assert result.queries == 2
    assert any(row.get("auto") is True for row in result.trace)
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_runs_auto_queries_after_fixed_queries -q
```

Expected: fail because `query_planner` is not supported and `auto` is only a skipped trace.

- [ ] **Step 3: Add query planner hook**

Change `run_web_evidence` signature:

```python
query_planner: Any | None = None,
```

Add helper:

```python
async def _auto_queries(
    *,
    query_planner: Any | None,
    company_name: str,
    profile: dict[str, Any],
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
) -> list[str]:
    web = indicator.web_search
    if web is None or not web.auto.enabled or web.auto.max_queries <= 0:
        return []
    if query_planner is None:
        return []
    queries = await query_planner(
        company_name=company_name,
        profile=profile,
        module=module,
        label=label,
        indicator=indicator,
        intent=web.auto.intent,
        max_queries=web.auto.max_queries,
    )
    return [str(query).format(company_name=company_name) for query in queries[: web.auto.max_queries]]
```

In the main loop:

```python
fixed_queries = _render_queries(company_name=company_name, indicator=indicator)
planned_auto_queries = await _auto_queries(...)
for query, is_auto in [(query, False) for query in fixed_queries] + [(query, True) for query in planned_auto_queries]:
    ...
```

Set query row `"auto": is_auto`.

- [ ] **Step 4: Keep no-LLM behavior safe**

When no `query_planner` is provided, write a skipped auto trace:

```python
if indicator.web_search.auto.enabled and query_planner is None:
    trace.append(_auto_trace(module, label, indicator, key))
```

This preserves current behavior for normal runs until an LLM planner is wired.

- [ ] **Step 5: Run auto query test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_runs_auto_queries_after_fixed_queries -q
```

Expected: pass.

### Task 6: Wire LLM Auto Query Planner

**Files:**

- Modify: `src/xft/pipeline/recommender/web_evidence.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write planner unit test with monkeypatched AI client**

Add a small test for a new helper `_plan_auto_queries_with_llm`:

```python
@pytest.mark.asyncio
async def test_plan_auto_queries_with_llm_returns_bounded_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMessage:
        content = '{"queries": ["测试公司 差旅 招聘", "测试公司 费控 系统", "多余 查询"]}'

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        async def create(self, **kwargs: object) -> object:
            return type("Resp", (), {"choices": [FakeChoice()]})()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("xft.pipeline.recommender.web_evidence.get_ai_client", lambda: FakeClient())
    monkeypatch.setattr("xft.pipeline.recommender.web_evidence.settings.llm_api_key", "test")

    indicator = IndicatorConfig.model_validate(
        {
            "indicator_id": "travel",
            "indicator_name": "差旅线索",
            "evaluator": "llm",
            "standard": "判断差旅需求",
            "web_search": {"auto": {"enabled": True, "max_queries": 2, "intent": "查差旅公开线索"}},
        }
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
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_plan_auto_queries_with_llm_returns_bounded_queries -q
```

Expected: fail because helper does not exist.

- [ ] **Step 3: Implement LLM planner**

In `web_evidence.py`, import:

```python
import json
from openai import OpenAIError
from pydantic import BaseModel, ValidationError
from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.settings import settings
```

Add:

```python
class _AutoQueryPayload(BaseModel):
    queries: list[str]
```

Add:

```python
async def _plan_auto_queries_with_llm(
    *,
    company_name: str,
    profile: dict[str, Any],
    module_id: str,
    module_name: str,
    label_id: str,
    label_name: str,
    indicator: IndicatorConfig,
    max_queries: int,
    intent: str,
) -> list[str]:
    if not (settings.llm_api_key or settings.minimax_api_key):
        return []
    system = (
        "你是企业推荐系统的Web搜索词规划器。"
        "只输出JSON，字段为queries。"
        "queries最多包含指定数量的中文搜索词。"
        "搜索词必须围绕公司名和指标判断需要的信息，不要编造事实。"
    )
    user_payload = {
        "company_name": company_name,
        "company_profile": {
            "company_name": profile.get("company_name"),
            "credit_code": profile.get("credit_code"),
            "industry": profile.get("basic", {}).get("industry") if isinstance(profile.get("basic"), dict) else None,
        },
        "module": {"id": module_id, "name": module_name},
        "label": {"id": label_id, "name": label_name},
        "indicator": {
            "id": indicator.indicator_id,
            "name": indicator.indicator_name,
            "standard": indicator.standard,
            "prompt": indicator.prompt,
        },
        "intent": intent,
        "max_queries": max_queries,
    }
    client = get_ai_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.0,
            timeout=30,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = _AutoQueryPayload.model_validate(json.loads(extract_json(raw)))
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, RuntimeError, TypeError, ValueError):
        return []
    return [query.strip() for query in parsed.queries if query.strip()][:max_queries]
```

Wire default planner in `run_web_evidence`:

```python
planner = query_planner or _plan_auto_queries_with_llm
```

Pass enough fields to the helper.

- [ ] **Step 4: Run planner test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_plan_auto_queries_with_llm_returns_bounded_queries -q
```

Expected: pass.

### Task 7: Update Config Examples Without Mass-Rewriting All Modules

**Files:**

- Modify: `config/recommend/sales_recommendation/business_modules.d/差旅报销.yaml`
- Modify: `config/recommend/sales_recommendation/business_modules.d/日常报销.yaml`
- Modify: `config/recommend/sales_recommendation/business_modules.d/假勤管理.yaml`
- Test: scenario validation

- [ ] **Step 1: Pick representative indicators**

Edit only a small number of indicators first:

- Keep existing `llm_web` indicators that are Web-first.
- Convert one indicator in `差旅报销.yaml` from implicit Web-first to explicit:

```yaml
web_search:
  when: always
  effect: llm_evidence
  fixed_queries:
    - "{company_name} 差旅 报销"
```

- Add one `hybrid` indicator with:

```yaml
web_search:
  when: insufficient
  effect: llm_evidence
  fixed_queries:
    - "{company_name} 差旅 管理 招聘"
  auto:
    enabled: true
    max_queries: 2
    intent: "查企业公开差旅、商旅、报销、费控需求线索"
```

- Add or modify one `rule` indicator with:

```yaml
web_search:
  when: rule_not_matched
  effect: possible_on_evidence
  fixed_queries:
    - "{company_name} 工厂 分支机构"
```

- [ ] **Step 2: Validate scenario**

Run:

```bash
uv run xft scenario validate config/recommend/sales_recommendation
```

Expected output includes:

```json
"scenario_id": "sales_recommendation"
```

- [ ] **Step 3: Avoid broad config churn**

Do not rewrite every existing `llm_web` indicator in this task. Existing config remains valid through defaults. Expand module configs later after smoke-testing noise.

### Task 8: Update Documentation

**Files:**

- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/SCORING.md`
- Modify: `docs/SMOKE.md`
- Modify: `docs/NEXT.md`
- Modify: `docs/TECH_DEBT.md`

- [ ] **Step 1: Update README evaluator table**

Replace the current evaluator table with:

```markdown
| evaluator | 适合场景 | 是否需要 LLM | Web 角色 |
| --- | --- | --- | --- |
| `rule` | 本地结构化字段可确定判断 | 否 | 可选，仅补线索；最多提升到 `possible` |
| `llm` | 本地画像文本可判断，但可能证据不足 | 是 | 可选，通常 `when: insufficient` |
| `hybrid` | 规则先行，必要时 LLM 复核 | 可选 | 可选，通常在规则/本地证据不足时补证 |
| `llm_web` | 公开网页才可能有的信息 | 是 | 必选，默认 `when: always` |
```

- [ ] **Step 2: Add config examples**

Add examples for:

- `llm_web` Web-first.
- `llm` insufficient fallback.
- `hybrid` fixed plus auto fallback.
- `rule` possible-on-evidence.

- [ ] **Step 3: Update architecture references**

Replace "`web_evidence` only runs `llm_web` fixed queries" with:

```markdown
`web_evidence` 在 `--with-web` 开启时执行指标级 Web policy：
固定查询先执行，配置允许时再执行少量 LLM 自动生成查询。Web 证据按 indicator key 回填给业务判断节点。
```

- [ ] **Step 4: Update smoke docs**

Add:

```bash
uv run xft recommend --with-web --llm-debug --scenario config/recommend/sales_recommendation "企业名称"
```

Expected artifacts:

```text
web_queries.jsonl
web_results.jsonl
web_trace.json
indicator_evidence.json
```

### Task 9: Full Verification

**Files:**

- No source edits.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/test_recommendation.py tests/test_scenario_bundle.py tests/test_xft_cli.py -q
```

Expected: all pass.

- [ ] **Step 2: Run lint**

Run:

```bash
uv run ruff check src tests scripts
```

Expected: pass.

- [ ] **Step 3: Run type check**

Run:

```bash
uv run mypy src
```

Expected: pass.

- [ ] **Step 4: Run full tests**

Run:

```bash
uv run pytest
```

Expected: all pass.

- [ ] **Step 5: Run scenario validation**

Run:

```bash
uv run xft scenario validate config/recommend/sales_recommendation
```

Expected: validation succeeds and reports the loaded business modules.

---

## Rollout Notes

- Keep `--with-web` as the explicit switch. No command should search Web unless the user enables business Web.
- Keep `llm_web` because it communicates Web-first intent clearly.
- Let `llm + web_search.when: always` be functionally equivalent to `llm_web`, but document `llm_web` as the preferred form for Web-first indicators.
- Do not let `rule` become a hidden LLM/Web evaluator. A rule can become `possible` from Web evidence only when explicitly configured.
- Keep automatic query generation bounded:
  - per indicator max queries: 0-5
  - default max queries: 0 unless explicitly enabled
  - no auto generation when LLM credentials are absent
- Treat Web evidence as noisy. It should be traceable, reviewable, and never silently overwrite local facts.

---

## Self-Review

Spec coverage:

- `llm` and `hybrid` can use Web when local evidence is insufficient: covered by Tasks 1-3 and 5-6.
- Fixed search terms: covered by Task 3.
- System-inferred search terms: covered by Tasks 5-6.
- `rule` can support Web without breaking deterministic semantics: covered by Task 4.
- `llm_web` retention decision: documented in Product Decision and enforced by Tasks 1-3.
- Documentation updates: covered by Task 8.
- Verification: covered by Task 9.

Placeholder scan:

- No TBD/TODO placeholders.
- Each implementation task has files, tests, commands, and expected outcome.

Type consistency:

- `WebSearchWhen`, `WebSearchEffect`, `WebAutoConfig`, `WebSearchDecision`, and `should_search_indicator` are introduced before later tasks reference them.
