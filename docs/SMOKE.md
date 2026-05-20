# 冒烟验收

本文档记录日常最小验收流程。目标是每次改配置或重构后，确认推荐主线没有坏。

## 1. 场景配置校验

```bash
uv run xft scenario validate config/recommender/xft
```

预期输出包含：

```json
{
  "scenario_id": "sales_recommendation",
  "web_enabled": true,
  "modules": 7
}
```

## 2. 推荐流水线冒烟

离线运行，不调用 LLM，不搜索 Web：

```bash
uv run xft recommend --no-llm \
  --scenario config/recommender/xft \
  "企业名称"
```

预期：

- 命令退出码为 0。
- 输出 `[success]` 或可接受的 `partial`。
- 生成 `result.json`。
- 生成 `report.md`。
- 生成 `label_result.json`。
- 生成 `indicator_evidence.json`。
- 生成 `profile.json`、`decision_trace.json`、`config_manifest.json`。
- 不生成 `dimension_analysis.json`、`match_results.json`、`internal_result.json`。

`result.json` 至少应包含：

```text
CompanyName
Module
AcceptanceResult
LabelResult
MarketingPoint
Conclusion
```

## 3. LLM 和业务 Web 可选验收

调试 LLM：

```bash
uv run xft recommend --llm-debug \
  --scenario config/recommender/xft \
  "企业名称"
```

启用业务指标级 Web policy：

```bash
uv run xft recommend --with-web \
  --scenario config/recommender/xft \
  "企业名称"
```

刷新业务 Web 缓存：

```bash
uv run xft recommend --with-web --web-refresh \
  --scenario config/recommender/xft \
  "企业名称"
```

业务 Web 预期额外生成：

```text
indicator_evidence.json
web_queries.jsonl
web_results.jsonl
web_trace.json
```

抽查重点：

- `llm_web` 指标应有 Web-first 查询。
- `llm/hybrid` 指标只在配置的 `when` 条件下补证。
- `rule` 指标的 Web 证据最多产生 `possible`，不应直接变成 `matched`。

## 4. 快速测试集

```bash
uv run pytest \
  tests/test_recommender.py \
  tests/test_scenario_bundle.py \
  tests/test_xft_cli.py \
  tests/test_batch_delivery.py \
  tests/test_recommendation.py \
  tests/test_runtime_calibration.py \
  -q
```

## 5. 完整质量门禁

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest
```
