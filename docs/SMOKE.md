# 冒烟验收

本文档记录推荐主线的最小验收流程。每次改配置、重构推荐代码或更新依赖后，至少跑完这里的检查。

## 1. 场景配置校验

```bash
uv run xft scenario validate config/recommender/xft
```

预期包含：

```json
{
  "scenario_id": "sales_recommendation",
  "web_enabled": true,
  "modules": 7
}
```

## 2. 离线推荐

不调用 LLM，不搜索 Web：

```bash
uv run xft recommend --no-llm \
  --scenario config/recommender/xft \
  "企业名称"
```

预期：

- 命令退出码为 0。
- 控制台输出 `[success]` 或可接受的 `[partial]`。
- 输出目录位于 `outputs/recommender/xft/<run_id>/`。
- 至少生成：

```text
result.json
report.md
label_result.json
indicator_evidence.json
profile.json
decision_trace.json
config_manifest.json
scenario_resolved.json
llm_calls.jsonl
llm_metrics.json
```

`result.json` 至少应包含：

```text
CompanyName
Module
AcceptanceResult
LabelResult
MarketingPoint
Conclusion
```

## 3. LLM 验收

```bash
uv run xft recommend --llm-debug \
  --scenario config/recommender/xft \
  "企业名称"
```

检查：

- `llm_metrics.json` 中 `failed` 不应异常偏高。
- `llm_calls.jsonl` 中失败记录有清晰错误。
- `label_result.json` 中 LLM 指标有 `current_status` 和 `evidence`。

## 4. Web 验收

```bash
uv run xft recommend --with-web \
  --scenario config/recommender/xft \
  "企业名称"
```

刷新缓存：

```bash
uv run xft recommend --with-web --web-refresh \
  --scenario config/recommender/xft \
  "企业名称"
```

额外检查：

```text
web_queries.jsonl
web_results.jsonl
web_trace.json
indicator_evidence.json
```

抽查重点：

- `llm_web` 指标应先执行 Web 查询。
- `llm_web` 无实际 Web 证据时应输出 `unknown`，不空证据调用 LLM。
- `llm` / `hybrid` 只在指标 `web_search.when` 条件满足时补证。
- `rule` 配 `possible_on_evidence` 时，Web 证据最多提升到 `possible`。
- 查询词应包含指标词，不应都是 `{company_name} 官网` 这类泛查询。

## 5. 批量和校准冒烟

批量推荐：

```bash
uv run xft recommend \
  --company-list company.txt \
  --no-llm \
  --limit 3
```

校准：

```bash
uv run xft calibrate \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 3
```

启用 Web/LLM 校准：

```bash
uv run xft calibrate \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --with-llm \
  --with-web \
  --limit 3
```

## 6. 快速测试集

```bash
uv run pytest \
  tests/test_recommender.py \
  tests/test_scenario_bundle.py \
  tests/test_xft_cli.py \
  tests/test_batch_delivery.py \
  tests/test_business_recommendation.py \
  tests/test_runtime_calibration.py \
  -q
```

## 7. 完整质量门禁

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
```
