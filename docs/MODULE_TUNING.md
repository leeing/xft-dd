# 模块调优流程

本文档用于调试推荐模块配置，重点是选择 `rule` / `hybrid` / `llm` / `llm_web`，以及调整 Web 搜索词。

## 推荐顺序

1. 先审计配置：

```bash
uv run xft scenario audit config/recommender/xft
```

2. 只跑目标模块，关闭 LLM 和 Web，先验证本地规则：

```bash
uv run xft recommend --no-llm --module 个税管理 "企业名称"
```

3. 开启 Web，观察哪些指标真的需要补证：

```bash
uv run xft recommend --with-web --module 个税管理 "企业名称"
```

4. 开启 LLM debug，检查 prompt 和证据是否足够：

```bash
uv run xft recommend --with-web --llm-debug --module 个税管理 "企业名称"
```

5. 精调单个指标：

```bash
uv run xft recommend --with-web --llm-debug --module 个税管理 --indicator 个税相关招聘 "企业名称"
```

## 看哪些文件

优先看：

```text
logs/<run_id>.log
indicator_evidence.json
web_trace.json
llm_calls.jsonl
label_result.json
```

`logs/<run_id>.log` 的开头有“调优建议摘要”，先处理：

- `unknown 指标`
- `无证据指标`
- `Web 未搜索`
- `Web 零结果`
- `Web 全文复核过滤`
- `LLM 失败`

## evaluator 选择

| evaluator | 使用条件 |
| --- | --- |
| `rule` | DuckDB 字段、明细表或企业画像能明确判断 |
| `hybrid` | 本地规则能给强信号，但还需要 LLM 解释模糊证据 |
| `llm` | 有本地证据，需要综合推理 |
| `llm_web` | 只能依赖公开网页判断，DuckDB 无法覆盖 |

优先把能结构化判断的指标做成 `rule` 或 `hybrid`。`llm_web` 保留给真正需要公开网页的指标。

## 搜索词原则

好的查询词应包含：

- `{company_name}`
- 指标词
- 业务场景词

示例：

```yaml
fixed_queries:
  - "{company_name} 个税 薪酬 招聘"
  - "{company_name} 社保 个税 管理"
```

避免：

```yaml
fixed_queries:
  - "{company_name} 官网"
  - "{company_name} 新闻"
```

## Web policy 调整

| 场景 | 推荐配置 |
| --- | --- |
| `llm_web` 必须查公开网页 | `when: always` |
| 本地证据不足才补证 | `when: insufficient` |
| 规则未命中才找公开线索 | `when: rule_not_matched` |
| Web 只能补充线索，不能直接命中 | `effect: possible_on_evidence` |

如果 log 显示 `Web 未搜索`，先看原因：

- `web_disabled`：命令没有加 `--with-web`
- `rule_already_matched`：规则已命中，无需搜索
- `local_evidence_sufficient`：本地证据足够，无需搜索
- `rule_result_required`：规则尚未运行，不应提前搜索

如果 log 显示 `Web 零结果`，优先改 `fixed_queries`，不要先改代码。

如果 log 显示 `fetch_filtered` 大于 0，说明搜索摘要通过了初筛，但 crawl4ai 抓到的正文没有通过二次相关性复核。常见原因：

- `full_text_missing_company`：正文没有公司名或统一社会信用代码。
- `full_text_missing_indicator_terms`：正文没有指标词或查询词。

这种情况通常优先调整搜索词，让结果直接落到更具体的页面，而不是官网首页、列表页或泛新闻页。
