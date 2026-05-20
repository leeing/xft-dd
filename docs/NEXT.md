# 下一步计划

本文档记录当前推荐主线的近期工作重点。它只描述当前仓库仍需要推进的事项，不记录已删除流水线的历史迁移过程。

## 当前基线

默认场景：

```text
config/recommender/xft
```

推荐主链路：

```text
data_gather -> web_evidence -> recommend -> save
```

核心配置：

```text
scenario.yaml
modules.yaml
modules.d/*.yaml
web_search.yaml
```

核心产物：

```text
result.json
report.md
label_result.json
indicator_evidence.json
profile.json
decision_trace.json
llm_calls.jsonl
llm_metrics.json
scenario_resolved.json
config_manifest.json
```

启用 Web 时额外关注：

```text
web_queries.jsonl
web_results.jsonl
web_trace.json
```

## 近期优先级

### 1. 建立真实业务标注样本

目标：用业务人员认可的样本校准推荐准确性。

建议：

1. 准备 5-10 家真实企业。
2. 由业务人员标注期望推荐模块和可接受模块。
3. 运行校准并复盘错配。

标注文件格式：

```csv
company_name,expected_top_module,acceptable_modules,comment
某公司,日常报销,日常报销；差旅报销,人工标注说明
```

校准命令：

```bash
uv run xft calibrate \
  --scenario config/recommender/xft \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --with-llm \
  --with-web \
  --limit 10
```

### 2. 继续治理模块配置

当前配置治理方向：

1. 优先把能用本地结构化证据判断的指标改成 `rule` 或 `hybrid`。
2. 保留为 `llm_web` 的指标必须有指标专用 `fixed_queries`。
3. 所有 `data_sources.type=table` + `op=text_contains` 必须配置具体 `keywords`。
4. 检查接受度门槛是否符合业务预期，必要时调整 `modules.yaml` 的 `acceptance_policy`。

建议治理顺序：

```text
日常报销
销项发票
进项发票
对公报账
个税管理
```

每改一个模块后，至少跑：

```bash
uv run xft scenario validate config/recommender/xft
uv run xft recommend --no-llm --scenario config/recommender/xft "企业名称"
uv run xft recommend --with-web --llm-debug --scenario config/recommender/xft "企业名称"
```

### 3. 抽查 Web 证据质量

目标：确保 Web 补证提升判断质量，而不是引入噪声。

抽查方法：

1. 选 2-3 家企业运行 `--with-web --llm-debug`。
2. 打开 `web_trace.json`，检查查询词和过滤后结果。
3. 打开 `indicator_evidence.json`，确认 `source_type=web` 的证据能支撑指标判断。
4. 根据噪声调整指标的 `web_search.when/effect/fixed_queries/auto`。
5. 必要时调整 `web_search.yaml` 的 provider 和 `execution.max_results_per_query`。

### 4. 沉淀第二个业务场景

当前正式场景是 `sales_recommendation`。新增场景时建议复制默认目录：

```bash
cp -R config/recommender/xft config/recommender/<new_scenario>
```

新场景应独立维护：

```text
scenario.yaml
modules.yaml
modules.d/*.yaml
web_search.yaml
```

验证方式：

1. 用同一批企业分别运行两个场景。
2. 对比两个场景的 `result.json`。
3. 对比校准报告中的 top1 命中率和可接受命中率。

### 5. 改善业务交付形态

当前交付已经有 JSON、Markdown、CSV 和 manifest。后续可增强：

- 批量结果 `.xlsx` 汇总。
- 批量结果 zip 包。
- 面向业务复盘的错配清单。
- Web 证据人工复核表。

## 暂不推进

- 不新增独立 Web 抽取流水线。
- 不新增新的评分引擎。
- 不引入新的大框架。
- 不为单个模块做过度抽象，优先保持 YAML 配置可读。
