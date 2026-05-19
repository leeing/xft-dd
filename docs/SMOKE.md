# 冒烟验收

本文档记录日常最小验收流程。目标是每次改配置或重构后，先确认推荐主线和尽调入口没有坏。

## 1. 场景配置校验

```bash
uv run xft scenario validate config/recommend/sales_recommendation
uv run xft scenario validate config/recommend/bank_marketing
```

## 2. 推荐流水线冒烟

离线运行，不调用 LLM，不搜索 Web：

```bash
uv run xft recommend --no-llm \
  --scenario config/recommend/sales_recommendation \
  "企业名称"
```

预期：

- 命令退出码为 0。
- 输出 `[success]` 或可接受的 `partial`。
- 生成 `report.md`。
- 生成最终业务结果 `result.json`。
- 生成全量业务指标明细 `business_label_result.json`。
- 生成 `dimension_analysis.json`、`profile.json`、`decision_trace.json`。
- 不再生成 `match_results.json` 或 `internal_result.json`。

`result.json` 至少应包含：

```text
CompanyName
Module
AcceptanceResult
LabelResult
MarketingPoint
Conclusion
```

## 3. Web / LLM 可选验收

复用已有 Web 证据：

```bash
uv run xft recommend --with-web-evidence \
  --scenario config/recommend/sales_recommendation \
  "企业名称"
```

缺缓存时自动搜索：

```bash
uv run xft recommend --with-web \
  --scenario config/recommend/sales_recommendation \
  "企业名称"
```

调试 LLM：

```bash
uv run xft recommend --llm-debug \
  --scenario config/recommend/sales_recommendation \
  "企业名称"
```

## 4. 企业尽调流水线冒烟

使用 dry-run 只预览搜索计划：

```bash
uv run xft diligence --dry-run "企业名称"
```

预期：

- 命令退出码为 0。
- 输出目标企业名。
- 输出 active dimensions。
- 输出 `dry-run complete, no external calls made`。

## 5. 快速测试集

```bash
uv run pytest \
  tests/test_recommender.py \
  tests/test_scenario_bundle.py \
  tests/test_xft_cli.py \
  tests/test_batch_delivery.py \
  tests/test_business_recommendation.py \
  -q
```

## 6. 完整质量门禁

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```
