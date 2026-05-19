# NEXT.md

本文档记录当前优先级。项目已经完成旧推荐链删除，下一步集中打磨 `business_modules.yaml` 驱动的 `rule / llm / hybrid` 推荐体系。

## 当前状态

推荐主线：

```text
data_gather -> dimension_analyze -> web_evidence -> business_recommend -> save
```

已删除：

```text
llm_match_node.py
llm_recommend_node.py
recommendation_normalizer.py
src/xft/scoring/
products.yaml
scoring_policy.yaml
match_results.json
internal_result.json
```

当前核心产物：

```text
result.json
business_label_result.json
profile.json
dimension_analysis.json
decision_trace.json
llm_calls.jsonl
llm_metrics.json
config_manifest.json
report.md
```

## 当前优先级

### 1. 保证推荐主线正确运行

必须稳定：

```bash
uv run xft scenario validate config/scenarios/sales_recommendation
uv run xft recommend --no-llm --scenario config/scenarios/sales_recommendation "企业名称"
uv run xft recommend --with-web-evidence --scenario config/scenarios/sales_recommendation "企业名称"
```

关注点：

- `result.json` 是唯一最终业务结果。
- `business_label_result.json` 能解释每个模块、标签、指标。
- `decision_trace.json` 能解释 rule、llm、hybrid 的判断过程。
- 不再出现旧产物 `internal_result.json`、`match_results.json`。

### 2. 业务配置调优

业务人员优先修改：

| 想调什么 | 文件 |
| --- | --- |
| 模块、标签、指标、分数、话术 | `business_modules.yaml` |
| 本地证据字段、维度、Web 搜索词 | `analysis_dimensions.yaml` |
| 本地证据足够时是否跳过 Web | `evidence_policy.yaml` |
| Web provider、抓取、缓存策略 | `web_search.yaml` |
| Web 抽取模型 | `web_extract_llm.yaml` |
| Web 抽取提示词 | `prompts/extract_evidence_system.md` |

下一步建议准备 5-10 家人工标注样本，跑：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

### 3. Web 证据质量抽查

目标：确认 Web 补证真正提升推荐质量，而不是引入噪声。

建议：

1. 选 2-3 家企业运行 `--with-web --llm-debug`。
2. 人工检查 `dimension_analysis.json` 的 `web_evidence`。
3. 检查 `decision_trace.json` 中 Web plan 是否合理。
4. 根据噪声调整：
   - `analysis_dimensions.yaml` 搜索词
   - `web_search.yaml` blocked domains
   - `web_extract_llm.yaml`
   - `prompts/extract_evidence_system.md`

### 4. 第二真实场景

`bank_marketing` 当前仍是继承示例，不是真实银行营销验收场景。

建议：

1. 复制或继承 `sales_recommendation`。
2. 业务人员独立维护一份银行营销版 `business_modules.yaml`。
3. 用相同企业分别跑两个场景，对比 `result.json` 差异。

## 近期不做

- 不恢复旧产品评分引擎。
- 不恢复 `products.yaml` 和 `scoring_policy.yaml`。
- 不继续扩展旧 `diligence` 链路，只保证入口可用。
- 不增加新的抽象框架，先保证推荐质量和配置可读性。
