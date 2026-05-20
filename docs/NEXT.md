# NEXT.md

本文档记录当前优先级。项目已经完成推荐主链路聚焦：旧维度分析、旧 Web enrichment、旧 evidence policy 和旧产品评分链路均已移除。下一步集中打磨业务模块配置质量。

截至 2026-05-20，`假勤管理` 和 `差旅报销` 已作为配置治理样板完成一轮优化：能用本地结构化证据判断的指标优先改为 `rule` / `hybrid`，只保留确实依赖公开网页的指标为 `llm_web`。

## 当前状态

推荐主线：

```text
data_gather -> web_evidence -> recommend -> save
```

当前核心配置：

```text
config/recommend/sales_recommendation/
  scenario.yaml
  modules.yaml
  modules.d/*.yaml
  web_search.yaml
```

当前核心产物：

```text
result.json
label_result.json
indicator_evidence.json
profile.json
decision_trace.json
llm_calls.jsonl
llm_metrics.json
scenario_resolved.json
config_manifest.json
report.md
```

已删除：

```text
analysis_dimensions.yaml
evidence_policy.yaml
web_extract_llm.yaml
dimension_analysis.json
web_evidence.jsonl
match_results.json
internal_result.json
xft web
warehouse web-import
```

## 当前优先级

### 1. 保证推荐主线正确运行

必须稳定：

```bash
uv run xft scenario validate config/recommend/sales_recommendation
uv run xft recommend --no-llm --scenario config/recommend/sales_recommendation "企业名称"
uv run xft recommend --with-web --scenario config/recommend/sales_recommendation "企业名称"
```

关注点：

- `result.json` 是唯一最终业务结果。
- `label_result.json` 能解释每个模块、标签、指标。
- `indicator_evidence.json` 能解释每个指标用了哪些本地或 Web 证据。
- 不再出现旧产物 `dimension_analysis.json`、`internal_result.json`、`match_results.json`。

### 2. 业务配置调优

业务人员优先修改：

| 想调什么 | 文件 |
| --- | --- |
| 全局分数、全局接受策略、模块目录 | `modules.yaml` |
| 单个产品模块的标签、指标、话术、规则 | `modules.d/<模块名>.yaml` |
| 业务 Web provider 和每次查询结果数量 | `web_search.yaml` |
| 场景输出目录和业务 Web 缓存目录 | `scenario.yaml` |

建议准备 5-10 家人工标注样本，跑：

```bash
uv run xft calibrate \
  --scenario config/recommend/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

根据错配案例调整 `modules.d/*.yaml`。

当前配置治理优先级：

1. 继续治理 `日常报销`、`销项发票` 中剩余的高比例 `llm_web` 指标。
2. 对所有 `data_sources.type=table` + `op=text_contains` 的指标检查 `keywords`，不能留空。
3. 对所有 `llm_web` 指标检查 `fixed_queries`，避免只写 `{company_name} 官网` / `{company_name} 新闻`。

### 3. 业务 Web 证据质量抽查

目标：确认指标级 `web_search` policy 能带来有效证据，而不是引入噪声。

建议：

1. 选 2-3 家企业运行 `--with-web --llm-debug`。
2. 人工检查 `web_trace.json` 和 `indicator_evidence.json`。
3. 根据噪声调整对应指标的 `web_search.when/effect/fixed_queries/auto`。
4. 必要时调整 `web_search.yaml` 的 provider 或 `max_results_per_query`。

### 4. 第二真实业务场景

当前仓库只保留 `sales_recommendation` 正式场景。新增场景建议：

1. 复制 `config/recommend/sales_recommendation/`。
2. 保留 `scenario.yaml`、`modules.yaml`、`web_search.yaml` 结构。
3. 为新场景维护独立 `modules.d/*.yaml`。
4. 用同一批企业对比不同场景的 `result.json`。

## 近期不做

- 不恢复旧产品评分引擎。
- 不恢复 `analysis_dimensions.yaml`、`evidence_policy.yaml`、`web_extract_llm.yaml`。
- 不恢复 `xft web` 或旧 Web enrichment。
- 不恢复旧企业调研链路；项目保持推荐单主线。
- 不增加新的抽象框架，先保证推荐准确性和配置可读性。
