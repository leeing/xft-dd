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

5. 只看某个标签，保留该标签下所有指标：

```bash
uv run xft recommend --with-web --llm-debug --module 个税管理 --label 多分支机构_集团化制造企业 "企业名称"
```

6. 精调单个指标，必须给出完整 `module -> label -> indicator` 语境：

```bash
uv run xft recommend --with-web --llm-debug --module 个税管理 --label 多分支机构_集团化制造企业 --indicator 招聘信息 "企业名称"
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

| 证据形态 | 推荐 evaluator | 配置要点 |
| --- | --- | --- |
| `company_profile` 字段或明细表能直接判断 | `rule` | 写清 `rule.source_field` 或 `data_sources`，结果可复现 |
| 有本地证据，但需要判断语义、归类或业务含义 | `llm` | 写清 `standard`、`prompt`、`evidence_hints` |
| 有硬规则可先挡一层，规则不够时再让 LLM 判断 | `hybrid` | 优先 `merge_policy: rule_first`，减少 LLM 成本 |
| 本地没有可靠数据，只能靠公开网页 | `llm_web` | 必须配置 `web_search`，没有 Web 证据时返回 `unknown` |

推荐顺序：

1. 能 `rule` 就不要 `llm`。
2. 有硬规则但还需要解释，优先 `hybrid`。
3. 本地证据足够但需要语义判断，才用 `llm`。
4. 只有公开网页才可能判断，才用 `llm_web`。

## company_profile 字段怎么用

`company_profile` 是推荐主表，`rule.source_field` 默认从这里取字段。常用字段：

| 字段 | 用途 |
| --- | --- |
| `industry` / `industry_big` / `industry_mid` / `industry_small` | 行业、细分行业、制造业属性 |
| `business_scope` | 主营业务、产品、服务类型 |
| `employee_count` | 企业规模、用工规模 |
| `labels` | 企业标签、科技资质、银行标签 |
| `ip_counts.patent` / `ip_counts.software` | 知识产权、研发属性 |
| `recent_recruitment_titles` / `recruitment_count` | 招聘和岗位信号 |
| `branch_count` | 分支机构、多区域经营 |
| `qualification_count` | 资质丰富度 |
| `outbound_investment_count` | 多法人主体、集团化经营 |
| `cross_border_flags` | 出口、跨境、海外业务线索 |
| `profile_completeness` | 画像是否足够完整 |

示例：

```yaml
evaluator: rule
standard: 企业存在专利
rule:
  source_field: ip_counts.patent
  op: ">"
  value: 0
```

读取招聘、分支、资质、对外投资、关键人员等明细时，用 `data_sources`，不要把明细表字段写进 `rule.source_field`。

## 让 LLM 代配指标

后续可以直接把业务意图交给 LLM，不必手写 YAML。建议提供这些信息：

```text
帮我配置一个推荐指标：
module: 假勤管理
label: 科技属性
indicator: 研发投入

业务含义:
企业公开材料中有研发投入、研发项目、研发中心、研发费用等证据，就认为具备研发投入属性。

希望策略:
优先使用本地 company_profile 和明细表；本地证据不足时再 Web 搜索；Web 有证据后让 LLM 判断。

可接受证据:
- company_profile.labels 中的高新技术企业、专精特新、科技型中小企业
- ip_counts.patent 或 ip_counts.software 大于 0
- 官网、新闻、年报、招股书中明确提到研发投入/研发项目/研发中心

搜索词倾向:
{company_name} 研发投入
{company_name} 研发中心
{company_name} 研发项目

验证企业:
企业名称
```

LLM 应按这个流程执行：

1. 找到 `config/recommender/xft/modules.d/<module>.yaml`。
2. 判断指标应使用 `rule`、`llm`、`hybrid` 还是 `llm_web`。
3. 修改 YAML，并保持 `module -> label -> indicator` 层级清楚。
4. 运行 `uv run xft scenario audit config/recommender/xft`。
5. 用 `uv run xft recommend --module <module> --label <label> --indicator <indicator> --with-web --llm-debug "<企业名称>"` 验证。
6. 交付本次配置选择、证据路径、Web 查询词、LLM 判断和后续调优建议。

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
