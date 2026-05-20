# Recommender Evaluation Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce recommender maintenance burden by simplifying the business evaluator parameter chain and decomposing `run_web_evidence()` into focused, testable units.

**Architecture:** Keep the public pipeline APIs stable. Introduce small internal context/data objects so shared runtime inputs are passed once, then split the Web evidence runner into cache/path setup, provider selection, indicator query planning, query execution, and result persistence.

**Tech Stack:** Python 3.12, asyncio, Pydantic models, pytest, ruff, mypy.

---

## File Structure

- Modify: `src/xft/pipeline/recommender/business_evaluator.py`
  - Add `BusinessEvaluationContext`.
  - Change internal evaluator helpers to accept `ctx` instead of repeating config/profile/LLM/semaphore arguments.
  - Keep `evaluate_recommendation(...)` public signature unchanged.

- Modify: `src/xft/pipeline/recommender/web_evidence.py`
  - Add internal dataclasses for run paths, run context, query spec, and accumulator.
  - Keep `run_web_evidence(...)` public signature unchanged.
  - Extract helper functions from the current large nested function.

- Modify: `tests/test_recommendation.py`
  - Add regression tests before refactoring the risky paths:
    - duplicate Web query reuse across indicators still creates per-indicator trace/evidence rows.
    - evaluator still records LLM failure exactly once when LLM call raises.

---

## Task 1: Lock Web Query Reuse Behavior

**Files:**
- Modify: `tests/test_recommendation.py`

- [ ] **Step 1: Add a regression provider and test**

Add this test near the other `run_web_evidence` tests:

```python
async def test_web_evidence_reuses_duplicate_query_per_indicator(tmp_path: Path) -> None:
    config = RecommendationConfig.model_validate(
        {
            "acceptance_policy": {"levels": [{"result": "低", "min_matched_labels": 0, "conclusion": "低"}]},
            "modules": [
                {
                    "module_id": "m",
                    "module_name": "模块",
                    "labels": [
                        {
                            "label_id": "a",
                            "label_name": "标签A",
                            "indicators": [
                                {
                                    "indicator_id": "rd",
                                    "indicator_name": "研发中心",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息显示有研发中心",
                                    "web_search": {"fixed_queries": ["{company_name} 研发中心"]},
                                }
                            ],
                        },
                        {
                            "label_id": "b",
                            "label_name": "标签B",
                            "indicators": [
                                {
                                    "indicator_id": "rd",
                                    "indicator_name": "研发中心",
                                    "evaluator": "llm_web",
                                    "standard": "公开信息显示有研发中心",
                                    "web_search": {"fixed_queries": ["{company_name} 研发中心"]},
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )

    calls = 0

    class DuplicateQueryProvider(_FakeBusinessProvider):
        async def search(self, query: str, *, dimension_id: str) -> object:
            nonlocal calls
            calls += 1
            return await super().search(query, dimension_id=dimension_id)

    result = await run_web_evidence(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        web_config_path=str(_write_business_web_config(tmp_path)),
        output_dir=tmp_path / "out",
        provider_factory=lambda _name, _config: DuplicateQueryProvider(),
        refresh=True,
    )

    assert calls == 1
    assert result.queries == 2
    assert "m.a.rd" in result.evidence
    assert "m.b.rd" in result.evidence
    assert {row["indicator_key"] for row in result.trace} == {"m.a.rd", "m.b.rd"}
```

- [ ] **Step 2: Run the new test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_reuses_duplicate_query_per_indicator -q
```

Expected: PASS before refactor.

---

## Task 2: Lock LLM Failure Event Behavior

**Files:**
- Modify: `tests/test_recommendation.py`

- [ ] **Step 1: Add a regression test**

Add this test near the other `evaluate_recommendation` LLM tests:

```python
async def test_business_evaluator_records_llm_failure_once(monkeypatch: pytest.MonkeyPatch) -> None:
    config = RecommendationConfig.model_validate(
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
                                    "indicator_id": "i",
                                    "indicator_name": "指标",
                                    "evaluator": "llm",
                                    "standard": "判断公开证据",
                                    "prompt": "判断公开证据。",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    async def fail_completion(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr("xft.pipeline.recommender.business_evaluator.settings.llm_api_key", "test")
    monkeypatch.setattr("xft.pipeline.recommender.business_evaluator.create_json_chat_completion", fail_completion)

    events: list[dict[str, Any]] = []
    result = await evaluate_recommendation(
        config=config,
        company_name="测试公司",
        profile={"company_name": "测试公司"},
        evidence={},
        use_llm=True,
        llm_events=events,
    )

    assert result is not None
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["name"] == "m.l.i"
    assert result.warnings == ["m.l.i: RuntimeError: boom"]
```

- [ ] **Step 2: Run the new test**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_business_evaluator_records_llm_failure_once -q
```

Expected: PASS before refactor.

---

## Task 3: Introduce BusinessEvaluationContext

**Files:**
- Modify: `src/xft/pipeline/recommender/business_evaluator.py`

- [ ] **Step 1: Add the context dataclass**

Add this after `_LlmIndicatorPayload`:

```python
@dataclass
class BusinessEvaluationContext:
    config: RecommendationConfig
    company_name: str
    profile: dict[str, Any]
    evidence: dict[str, list[dict[str, Any]]]
    web_trace_by_indicator: dict[str, list[dict[str, Any]]]
    use_llm: bool
    llm_debug: bool
    llm_events: list[dict[str, Any]]
    semaphore: asyncio.Semaphore

    @property
    def llm_available(self) -> bool:
        return self.use_llm and bool(settings.llm_api_key or settings.minimax_api_key)
```

If `dataclass` is not already imported, add:

```python
from dataclasses import dataclass
```

- [ ] **Step 2: Build the context in the public entrypoint**

In `evaluate_recommendation(...)`, replace the separate local values passed into `_evaluate_module(...)` with:

```python
    ctx = BusinessEvaluationContext(
        config=config,
        company_name=company_name,
        profile=profile,
        evidence=evidence,
        web_trace_by_indicator=web_trace_by_indicator,
        use_llm=use_llm,
        llm_debug=llm_debug,
        llm_events=llm_events,
        semaphore=semaphore,
    )
```

Change the gather call to:

```python
    module_results = await asyncio.gather(*[_evaluate_module(ctx=ctx, module=module) for module in config.modules])
```

- [ ] **Step 3: Run targeted tests**

Run:

```bash
uv run pytest tests/test_recommendation.py -q
```

Expected: PASS.

---

## Task 4: Collapse Evaluator Internal Signatures

**Files:**
- Modify: `src/xft/pipeline/recommender/business_evaluator.py`

- [ ] **Step 1: Update `_evaluate_module` and `_evaluate_label`**

Change signatures to:

```python
async def _evaluate_module(
    *,
    ctx: BusinessEvaluationContext,
    module: ModuleConfig,
) -> tuple[ModuleResult, list[str]]:
```

```python
async def _evaluate_label(
    *,
    ctx: BusinessEvaluationContext,
    module: ModuleConfig,
    label: LabelConfig,
) -> tuple[LabelResult, list[str]]:
```

Inside these functions:

```python
_evaluate_label(ctx=ctx, module=module, label=label)
_evaluate_indicator_with_fallback(ctx=ctx, module=module, label=label, indicator=indicator)
_acceptance(ctx.config, module, attributes_number, indicators_number)
_cap_acceptance_for_confidence(config=ctx.config, module=module, labels=labels, ...)
score = ctx.config.scoring.label_scores.score_for(label_result)
```

- [ ] **Step 2: Update indicator evaluators**

Change signatures to:

```python
async def _evaluate_indicator_with_fallback(
    *,
    ctx: BusinessEvaluationContext,
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
) -> tuple[IndicatorResult, str | None]:
```

```python
async def _evaluate_indicator(
    *,
    ctx: BusinessEvaluationContext,
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
) -> IndicatorResult:
```

```python
async def _evaluate_hybrid_indicator(
    *,
    ctx: BusinessEvaluationContext,
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
) -> IndicatorResult:
```

Use these substitutions inside the bodies:

```python
ctx.config
ctx.company_name
ctx.profile
ctx.evidence
ctx.web_trace_by_indicator
ctx.use_llm
ctx.llm_debug
ctx.llm_events
ctx.semaphore
ctx.llm_available
```

For example:

```python
indicator_evidence = _indicator_evidence(ctx.evidence, module, label, indicator)
if indicator.evaluator in ("llm", "llm_web", "hybrid") and ctx.use_llm:
    result = _llm_failure_indicator_result(
        config=ctx.config,
        module=module,
        label=label,
        indicator=indicator,
        profile=ctx.profile,
        indicator_evidence=indicator_evidence,
        exc=exc,
    )
```

- [ ] **Step 3: Update `_evaluate_llm_indicator` call sites only**

Keep `_evaluate_llm_indicator(...)` signature unchanged for now, but call it from context:

```python
async with ctx.semaphore:
    return await _evaluate_llm_indicator(
        config=ctx.config,
        module=module,
        label=label,
        indicator=indicator,
        company_name=ctx.company_name,
        profile=ctx.profile,
        indicator_evidence=indicator_evidence,
        llm_debug=ctx.llm_debug,
        llm_events=ctx.llm_events,
    )
```

- [ ] **Step 4: Remove obsolete noqa markers where possible**

After the signature collapse, remove `# noqa: PLR0913` from helpers that no longer need it:

```python
async def _evaluate_module(...)
async def _evaluate_label(...)
async def _evaluate_indicator_with_fallback(...)
async def _evaluate_indicator(...)
async def _evaluate_hybrid_indicator(...)
```

Do not force-remove `PLR0913` from public `evaluate_recommendation(...)` in this task.

- [ ] **Step 5: Run evaluator tests and linters**

Run:

```bash
uv run pytest tests/test_recommendation.py -q
uv run ruff check src/xft/pipeline/recommender/business_evaluator.py tests/test_recommendation.py
uv run mypy src
```

Expected: all pass.

---

## Task 5: Extract Web Evidence Run Data Objects

**Files:**
- Modify: `src/xft/pipeline/recommender/web_evidence.py`

- [ ] **Step 1: Add dataclasses**

Add these near `WebRunResult`:

```python
@dataclass(frozen=True)
class WebRunPaths:
    out_dir: Path
    queries_path: Path
    results_path: Path
    trace_path: Path
    evidence_path: Path


@dataclass(frozen=True)
class WebRunContext:
    company_name: str
    profile: dict[str, Any]
    web_config: Any
    provider_names: list[str]
    provider_factory: Any
    query_planner: Any
    local_evidence_map: dict[str, list[dict[str, Any]]]

    @property
    def resolved_company_name(self) -> str:
        return str(self.profile.get("company_name") or self.company_name)


@dataclass(frozen=True)
class WebQuerySpec:
    module: ModuleConfig
    label: LabelConfig
    indicator: IndicatorConfig
    indicator_key: str
    decision: WebSearchDecision
    query: str
    auto: bool
    provider_name: str


@dataclass
class WebAccumulator:
    query_rows: list[dict[str, Any]]
    result_rows: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    evidence: dict[str, list[dict[str, Any]]]
    query_index: int
    query_cache: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]

    @classmethod
    def create(cls) -> WebAccumulator:
        return cls(query_rows=[], result_rows=[], trace=[], evidence={}, query_index=0, query_cache={})

    def next_query_id(self) -> str:
        self.query_index += 1
        return f"bq_{self.query_index:04d}"
```

- [ ] **Step 2: Add path/cache helpers**

Add:

```python
def _web_paths(output_dir: str | Path) -> WebRunPaths:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return WebRunPaths(
        out_dir=out_dir,
        queries_path=out_dir / "web_queries.jsonl",
        results_path=out_dir / "web_results.jsonl",
        trace_path=out_dir / "web_trace.json",
        evidence_path=out_dir / "indicator_evidence.json",
    )


def _cached_web_result(paths: WebRunPaths) -> WebRunResult | None:
    if not paths.queries_path.exists() or not paths.results_path.exists():
        return None
    cached_queries = read_jsonl(paths.queries_path)
    cached_results = read_jsonl(paths.results_path)
    cached_evidence, cached_trace = _evidence_from_rows(cached_queries, cached_results)
    return WebRunResult(
        evidence=cached_evidence,
        trace=cached_trace,
        queries=len(cached_queries),
        results=len(cached_results),
        output_dir=str(paths.out_dir),
    )


def _empty_web_result(paths: WebRunPaths) -> WebRunResult:
    return WebRunResult(evidence={}, trace=[], queries=0, results=0, output_dir=str(paths.out_dir))
```

- [ ] **Step 3: Run targeted checks**

Run:

```bash
uv run ruff check src/xft/pipeline/recommender/web_evidence.py
uv run mypy src
```

Expected: all pass.

---

## Task 6: Extract Provider Selection and Query Planning

**Files:**
- Modify: `src/xft/pipeline/recommender/web_evidence.py`

- [ ] **Step 1: Add provider helper**

Add:

```python
def _enabled_provider_names(web_config: Any, providers: list[str] | None) -> list[str]:
    provider_names = providers or web_config.default_providers
    return [
        name for name in provider_names if web_config.providers.get(name, None) and web_config.providers[name].enabled
    ]
```

- [ ] **Step 2: Add query planning helper**

Add:

```python
async def _plan_web_queries(
    *,
    config: RecommendationConfig,
    ctx: WebRunContext,
) -> tuple[list[WebQuerySpec], list[dict[str, Any]]]:
    specs: list[WebQuerySpec] = []
    trace: list[dict[str, Any]] = []
    for module in config.modules:
        for label in module.labels:
            for indicator in label.indicators:
                if indicator.web_search is None:
                    continue
                key = indicator_key(module, label.label_id, indicator)
                local_evidence = ctx.local_evidence_map.get(key, [])
                decision = should_search_indicator(indicator=indicator, local_evidence=local_evidence, rule_result=None)
                if not decision.enabled:
                    trace.append(_skip_trace(module, label, indicator, key, decision))
                    continue
                fixed_queries = _render_queries(company_name=ctx.company_name, indicator=indicator)
                auto_queries = await _auto_queries(
                    query_planner=ctx.query_planner,
                    company_name=ctx.company_name,
                    profile=ctx.profile,
                    module=module,
                    label=label,
                    indicator=indicator,
                )
                if indicator.web_search.auto.enabled and not auto_queries:
                    trace.append(_auto_trace(module, label, indicator, key))
                query_specs = [(query, False) for query in fixed_queries] + [(query, True) for query in auto_queries]
                for query, is_auto in query_specs:
                    for provider_name in ctx.provider_names:
                        specs.append(
                            WebQuerySpec(
                                module=module,
                                label=label,
                                indicator=indicator,
                                indicator_key=key,
                                decision=decision,
                                query=query,
                                auto=is_auto,
                                provider_name=provider_name,
                            )
                        )
    return specs, trace
```

- [ ] **Step 3: Run the existing Web tests**

Run:

```bash
uv run pytest tests/test_recommendation.py -q
```

Expected: PASS.

---

## Task 7: Extract Web Query Execution

**Files:**
- Modify: `src/xft/pipeline/recommender/web_evidence.py`

- [ ] **Step 1: Add row cloning helper for query cache reuse**

Add:

```python
def _query_row_for_spec(
    cached_query_row: dict[str, Any],
    spec: WebQuerySpec,
) -> dict[str, Any]:
    return {
        **cached_query_row,
        "indicator_key": spec.indicator_key,
        "module_id": spec.module.module_id,
        "label_id": spec.label.label_id,
        "indicator_id": spec.indicator.indicator_id,
        "auto": spec.auto,
        "trigger_reason": spec.decision.reason,
        "when": spec.decision.when,
        "effect": spec.decision.effect,
    }
```

- [ ] **Step 2: Add accumulator helper**

Add:

```python
def _append_web_rows(
    *,
    acc: WebAccumulator,
    key: str,
    query_row: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    acc.query_rows.append(query_row)
    acc.result_rows.extend(rows)
    acc.trace.append(_trace_row(query_row, rows))
    if result_evidence := _result_evidence(query_row, rows):
        acc.evidence.setdefault(key, []).extend(result_evidence)
```

- [ ] **Step 3: Add execution helper**

Add:

```python
async def _execute_web_queries(
    *,
    ctx: WebRunContext,
    specs: list[WebQuerySpec],
    initial_trace: list[dict[str, Any]],
) -> WebAccumulator:
    acc = WebAccumulator.create()
    acc.trace.extend(initial_trace)
    for spec in specs:
        provider_cfg = ctx.web_config.providers[spec.provider_name]
        cache_key = f"{spec.query}:{spec.provider_name}"
        if cache_key in acc.query_cache:
            cached_query_row, cached_rows = acc.query_cache[cache_key]
            per_key_row = _query_row_for_spec(cached_query_row, spec)
            _append_web_rows(acc=acc, key=spec.indicator_key, query_row=per_key_row, rows=cached_rows)
            continue
        query_id = acc.next_query_id()
        query_row, rows = await _run_one_query(
            provider_name=spec.provider_name,
            provider_cfg=provider_cfg,
            provider_factory=ctx.provider_factory,
            query=spec.query,
            query_id=query_id,
            indicator_key=spec.indicator_key,
            module=spec.module,
            label=spec.label,
            indicator=spec.indicator,
            company_name=ctx.company_name,
            profile=ctx.profile,
            max_results=min(spec.indicator.web_search.max_results, ctx.web_config.execution.max_results_per_query),
            auto=spec.auto,
            decision=spec.decision,
        )
        acc.query_cache[cache_key] = (query_row, rows)
        _append_web_rows(acc=acc, key=spec.indicator_key, query_row=query_row, rows=rows)
    return acc
```

- [ ] **Step 4: Run query reuse regression**

Run:

```bash
uv run pytest tests/test_recommendation.py::test_web_evidence_reuses_duplicate_query_per_indicator -q
```

Expected: PASS.

---

## Task 8: Rewrite run_web_evidence Orchestration

**Files:**
- Modify: `src/xft/pipeline/recommender/web_evidence.py`

- [ ] **Step 1: Replace the body of `run_web_evidence`**

Keep the public signature, but replace the function body with:

```python
    """Execute indicator-level Web queries and convert results into evidence."""
    paths = _web_paths(output_dir)
    if not refresh and (cached := _cached_web_result(paths)):
        return cached
    if config is None:
        return _empty_web_result(paths)
    web_config = load_web_search_config(web_config_path)
    if not web_config.enabled:
        return _empty_web_result(paths)

    ctx = WebRunContext(
        company_name=company_name,
        profile=profile,
        web_config=web_config,
        provider_names=_enabled_provider_names(web_config, providers),
        provider_factory=provider_factory,
        query_planner=query_planner or _plan_auto_queries_with_llm,
        local_evidence_map=evidence or {},
    )
    specs, planning_trace = await _plan_web_queries(config=config, ctx=ctx)
    acc = await _execute_web_queries(ctx=ctx, specs=specs, initial_trace=planning_trace)
    write_jsonl(paths.queries_path, acc.query_rows)
    write_jsonl(paths.results_path, acc.result_rows)
    write_json(paths.trace_path, {"queries": acc.query_rows, "trace": acc.trace})
    write_json(paths.evidence_path, acc.evidence)
    return WebRunResult(
        evidence=acc.evidence,
        trace=acc.trace,
        queries=len(acc.query_rows),
        results=len(acc.result_rows),
        output_dir=str(paths.out_dir),
    )
```

- [ ] **Step 2: Remove obsolete local variables and noqa suppressions**

Remove `C901`, `PLR0912`, and `PLR0915` from `run_web_evidence`. Keep `PLR0913` because the public API still has many parameters:

```python
async def run_web_evidence(  # noqa: PLR0913
```

- [ ] **Step 3: Run Web evidence tests**

Run:

```bash
uv run pytest tests/test_recommendation.py -q
uv run ruff check src/xft/pipeline/recommender/web_evidence.py tests/test_recommendation.py
uv run mypy src
```

Expected: all pass.

---

## Task 9: Final Verification

**Files:**
- No code changes unless verification reveals an issue.

- [ ] **Step 1: Check complexity suppressions have shrunk**

Run:

```bash
rg -n "C901|PLR0912|PLR0913|PLR0915" src/xft/pipeline/recommender/web_evidence.py src/xft/pipeline/recommender/business_evaluator.py
```

Expected:
- `run_web_evidence` has at most `PLR0913`.
- Internal evaluator chain helpers no longer need `PLR0913`.
- Remaining `PLR0913` occurrences are public APIs or genuinely broad leaf functions such as `_evaluate_llm_indicator`.

- [ ] **Step 2: Run full project checks**

Run:

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
```

Expected:
- Ruff: `All checks passed!`
- Mypy: `Success: no issues found`
- Pytest: all tests pass.

- [ ] **Step 3: Review diff for public API stability**

Run:

```bash
git diff -- src/xft/pipeline/recommender/business_evaluator.py src/xft/pipeline/recommender/web_evidence.py tests/test_recommendation.py
```

Expected:
- No call sites outside these modules need changes.
- `evaluate_recommendation(...)` signature unchanged.
- `run_web_evidence(...)` signature unchanged.
- Behavior changes limited to internal organization.

---

## Self-Review

- Spec coverage: #3 is covered by Tasks 5-8; #4 is covered by Tasks 3-4. Regression tests are added before risky refactors.
- Placeholder scan: No task depends on unspecified behavior; each helper has concrete signatures and implementation snippets.
- Type consistency: `BusinessEvaluationContext`, `WebRunContext`, `WebQuerySpec`, and `WebAccumulator` are introduced before use; helper names are consistent across tasks.
