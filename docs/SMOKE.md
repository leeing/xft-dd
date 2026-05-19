# 两条流水线冒烟验收

本文档记录日常最小验收流程。目标是每次改配置或重构后，先确认当前两条流水线入口没有坏。

## 1. 场景配置校验

先确认推荐场景配置可解析：

```bash
uv run xft scenario validate config/scenarios/sales_recommendation
uv run xft scenario validate config/scenarios/bank_marketing
```

## 2. 产品推荐流水线冒烟

使用本地 DuckDB 数据和规则兜底，不调用 LLM，不搜索 Web：

```bash
uv run xft recommend --no-llm \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"
```

预期结果：

- 命令退出码为 0。
- 输出 `[success]` 或可接受的 `partial` 状态。
- 生成 `recommendation_runs/.../report.md`。
- 生成业务交付格式 `recommendation_runs/.../result.json`。
- 生成内部调试格式 `recommendation_runs/.../internal_result.json`。
- 生成标签判断中间结果 `recommendation_runs/.../business_label_result.json`。

`result.json` 至少应包含：

```text
CompanyName
Module
AcceptanceResult
LabelResult
MarketingPoint
Conclusion
```

如果要指定数据库：

```bash
uv run xft recommend --no-llm \
  --warehouse cache/company_warehouse.duckdb \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"
```

## 3. 企业尽调流水线冒烟

使用 dry-run 只预览搜索计划，不触发外部搜索、抓取或 LLM：

```bash
uv run xft diligence --dry-run "企业名称"
```

预期结果：

- 命令退出码为 0。
- 输出目标企业名。
- 输出 active dimensions。
- 输出 `dry-run complete, no external calls made`。

## 4. 快速测试集

如果只想跑和两条流水线入口关系最密切的测试：

```bash
uv run pytest \
  tests/test_recommender.py \
  tests/test_graph.py \
  tests/test_cli.py \
  tests/test_xft_cli.py \
  tests/test_scenario_bundle.py \
  tests/test_business_recommendation.py \
  -q
```

## 5. 完整质量门禁

提交前建议跑：

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

当前原则：先保证这两条流水线可运行，再做 Web/LLM 真实小批次验证。
