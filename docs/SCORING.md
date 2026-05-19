# 业务评分规则

当前推荐结果完全由 `business_modules.yaml` 驱动。旧的产品评分引擎 `xft.scoring`、`products.yaml`、`scoring_policy.yaml` 已删除。

## 结果层级

```mermaid
flowchart TB
    module["业务模块 module"] --> label["业务标签 label"]
    label --> indicator["业务指标 indicator"]
    indicator --> eval["evaluator: rule / llm / hybrid"]
    eval --> score["指标分"]
    score --> labelscore["标签分"]
    labelscore --> modulescore["模块分"]
    modulescore --> selected["selected_module"]
```

## 指标结果

每个指标输出：

```json
{
  "module_id": "daily_reimbursement",
  "label_id": "tech_attribute",
  "indicator_id": "tech_certification",
  "indicator_name": "科技企业-科技资质认证",
  "result": "matched",
  "confidence": "中",
  "score": 10,
  "current_status": "企业持有高新技术企业证书",
  "standard": "企业具备高新技术企业、专精特新、科技型中小企业等资质",
  "evidence": ["企业标签包含高新技术企业"],
  "evaluator": "hybrid",
  "hybrid_trace": {}
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `result` | `matched`、`possible`、`not_matched`、`unknown` |
| `confidence` | `高`、`中`、`低` |
| `score` | 指标分，由 `scoring.indicator_scores` 映射 |
| `current_status` | 当前证据下的企业状态 |
| `standard` | 配置中的判断标准 |
| `evidence` | 支撑判断的证据摘要 |
| `evaluator` | 采用 `rule`、`llm` 或 `hybrid` |
| `hybrid_trace` | hybrid 的规则/LLM 合并过程 |

## 分数配置

`business_modules.yaml` 顶部配置分数映射：

```yaml
scoring:
  indicator_scores:
    matched: 10
    possible: 5
    unknown: 0
    not_matched: 0
  label_scores:
    matched: 30
    possible: 15
    unknown: 0
    not_matched: 0
```

模块分数由：

```text
module.score = module.base_score + sum(label.score) + sum(indicator.score)
```

再限制在 0-100。

## Rule

`rule` 适合结构化字段明确的指标：

```yaml
evaluator: rule
standard: 企业标签包含高新技术企业
rule:
  source_field: labels
  op: contains
  value: 高新技术企业
```

常用操作符：

| op | 含义 |
| --- | --- |
| `exists` | 字段存在且非空 |
| `contains` | 字符串或列表包含某个值 |
| `contains_any` | 包含列表中的任一值 |
| `==` / `!=` | 等于 / 不等于 |
| `>` / `>=` / `<` / `<=` | 数值比较 |

## LLM

`llm` 适合需要综合文本、证据和业务标准推理的指标：

```yaml
evaluator: llm
standard: 企业存在跨区域经营、项目制管理或复杂报销场景
prompt: 判断企业是否可能存在复杂日常报销需求。
evidence_hints:
  - 企业规模
  - 分支机构
  - 招聘信息
  - Web 证据
```

LLM 输出会写入：

```text
business_label_result.json
decision_trace.json
llm_calls.jsonl
llm_metrics.json
```

## Hybrid

`hybrid` 是推荐的默认增强方式：先用规则处理硬证据，再让 LLM 处理模糊证据。

```yaml
evaluator: hybrid
merge_policy: rule_first
standard: 企业具备高新技术企业、专精特新、科技型中小企业等资质
rule:
  source_field: labels
  op: contains_any
  value:
    - 高新技术企业
    - 专精特新
    - 科技型中小企业
prompt: 判断企业是否具备科技型企业资质。
```

合并策略：

| `merge_policy` | 逻辑 |
| --- | --- |
| `rule_first` | 规则命中直接 `matched`，不调用 LLM；规则未命中再调用 LLM |
| `llm_confirm` | 规则给候选信号，LLM 负责确认；如 LLM 否定则降级 |
| `require_both` | 规则和 LLM 都命中才 `matched` |

## 标签与模块

标签根据指标聚合：

- 命中指标数达到 `min_matched_indicators`：标签 `matched`
- 有部分可能命中：标签 `possible`
- 全部证据不足：标签 `unknown`
- 其余：标签 `not_matched`

模块根据标签、指标和 `acceptance_policy` 生成接受度：

```yaml
acceptance_policy:
  levels:
    - result: 高
      min_matched_labels: 2
      conclusion: 建议优先推荐
    - result: 中
      min_matched_labels: 1
      conclusion: 建议跟进确认
    - result: 低
      min_matched_labels: 0
      conclusion: 暂不作为优先推荐
```

## 调优建议

| 现象 | 优先调整 |
| --- | --- |
| 明确字段命中了但没推荐 | `rule.source_field`、`op`、`value` |
| 证据模糊、规则太死 | 把 `rule` 改成 `hybrid` |
| LLM 判断太宽 | 收紧 `standard` 和 `prompt` |
| LLM 判断太保守 | 增加 `evidence_hints`，明确可接受的间接证据 |
| 模块分数整体偏高/偏低 | 调整 `base_score`、`indicator_scores`、`label_scores` |

## 验证

```bash
uv run xft scenario validate config/scenarios/sales_recommendation
uv run xft recommend --no-llm --scenario config/scenarios/sales_recommendation "企业名称"
uv run xft recommend --llm-debug --scenario config/scenarios/sales_recommendation "企业名称"
```
