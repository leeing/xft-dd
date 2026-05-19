# 企业分析与推荐平台

本项目是面向业务人员的企业分析工具。你把企业数据放进项目，选择一个业务场景，系统会读取本地企业画像，必要时补充 Web 证据，然后生成产品推荐结果和报告。

当前主力场景是“产品模块推荐”：根据企业行业、规模、资质、招投标、知识产权、招聘、风险等信息，判断企业更可能适合哪些产品模块，并解释推荐原因、证据来源和风险点。

技术细节（架构、数据流、模块边界、后续计划）见：

- [架构说明](docs/ARCHITECTURE.md)
- [推荐原理：规则引擎与 LLM 分工](docs/SCORING.md)

## 一句话流程

```text
准备数据 → 建本地数据库 → 选择场景 → 运行推荐 → 查看报告
```

如果需要 Web 补证，则流程是：

```text
准备数据 → 建本地数据库 → Web 搜索/抓取/抽取 → 入库 → 运行推荐 → 查看报告
```

## 你会得到什么

单家公司运行后，会在 `recommendation_runs/` 下生成一份结果目录，常用文件包括：

| 文件 | 用途 |
|------|------|
| `report.md` | 给人看的推荐报告 |
| `result.json` | 业务交付格式，包含推荐模块、命中标签、营销点和 KYC 问题 |
| `internal_result.json` | 内部推荐结果，包含规则评分、证据链和调试信息 |
| `business_label_result.json` | 业务标签判断中间结果，方便检查 rule / LLM 如何得出结论 |
| `llm_calls.jsonl` | 每次 LLM 调用的阶段、模型、耗时、完整响应和错误信息 |
| `llm_metrics.json` | LLM 调用次数、成功/失败数、累计耗时 |
| `decision_trace.json` | Web plan、搜索取舍、Rule 评分、LLM prompt/结论的统一审计链 |
| `profile.json` | 本次使用的企业画像 |
| `config_manifest.json` | 本次运行使用的配置文件和 hash，方便复现 |
| `scenario_resolved.json` | 场景配置解析结果 |

最常看的顺序是：

```text
result.json → 看最终推荐给业务/前端的结果
business_label_result.json → 看标签和指标为什么命中
internal_result.json → 看内部评分、证据链和工程调试信息
llm_calls.jsonl → 查外部 LLM 到底成功、失败还是超时
```

批量运行时，会额外生成批次汇总：

| 文件 | 用途 |
|------|------|
| `batch_summary.csv` | 每家公司推荐结果汇总 |
| `batch_summary.json` | 结构化批量汇总 |
| `batch_quality_report.md` | 批量质量报告，包含 Web 与 LLM 调用汇总 |
| `delivery_manifest.json` | 交付清单 |

## 1. 把项目跑起来

### 1.1 安装环境

项目使用 Python 3.12+ 和 `uv`。

```bash
uv sync
```

如果你第一次运行，建议先检查命令是否可用：

```bash
uv run xft --help
```

看到 `recommend`、`web`、`warehouse`、`scenario` 等子命令，就说明入口正常。

### 1.2 准备企业数据

把企业 JSON 数据放到项目根目录的 `data/` 目录下。

当前系统会从 `data/` 读取 Prophet/NewEnt 风格的企业 JSON 文件，并构建本地 DuckDB 数据库。`cache/` 目录由系统自动创建，无需手动准备。

### 1.3 构建本地数据库

```bash
uv run xft warehouse build --input data --output cache/company_warehouse.duckdb
```

这一步会把原始 JSON 导入到本地数据库，并生成推荐需要的企业画像和证据表。

### 1.4 运行一次离线推荐

离线推荐只使用本地 JSON 数据，不调用 LLM，也不搜索 Web：

```bash
uv run xft recommend --no-llm "企业名称"
```

不带 `--scenario` 时，CLI 默认使用 `config/scenarios/sales_recommendation` 场景。也就是默认读取这个目录下的：

- `scenario.yaml`
- `products.yaml`
- `business_modules.yaml`
- `analysis_dimensions.yaml`
- `scoring_policy.yaml`
- `evidence_policy.yaml`
- `web_search.yaml`
- `web_extract_llm.yaml`
- `prompts/*.md`

例如：

```bash
uv run xft recommend --no-llm "广东德美精细化工集团股份有限公司"
```

### 1.5 运行带 LLM 的推荐

如果已经配置好 LLM 密钥，可以运行：

```bash
uv run xft recommend "企业名称"
```

测试验证期建议打开 LLM 调试输出：

```bash
uv run xft recommend --llm-debug --llm-concurrency 4 "企业名称"
```

`--llm-debug` 会用块状格式打印每次 LLM 调用的阶段、模型、请求摘要、耗时、完整原始响应、错误类型和兜底路径。模型内部隐藏思考链不会打印；如果模型接口显式返回可见 reasoning 文本，会随完整响应一起出现。

`--llm-concurrency` 控制业务标签中多个 LLM 指标的并发调用数，默认是 `4`。产品匹配和推荐生成因为前后依赖，仍会按顺序执行。

### 1.6 运行带 Web 补证的推荐

如果希望本地数据不足时自动搜索 Web：

```bash
uv run xft recommend --with-web "企业名称"
```

如果只想使用已经入库的 Web 证据，不重新搜索：

```bash
uv run xft recommend --with-web-evidence "企业名称"
```

如果想忽略已有 Web 缓存，强制重新搜索：

```bash
uv run xft recommend --with-web --refresh-web "企业名称"
```

## 2. 常用运行方式

### 两条流水线最小验收

每次改配置或重构后，建议先跑这两条，确认当前主链路没有坏：

```bash
# 产品推荐流水线：只用本地数据和规则兜底，不调用 LLM / Web
uv run xft recommend --no-llm \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"

# 企业尽调流水线：只预览搜索计划，不触发外部调用
uv run xft diligence --dry-run "企业名称"
```

更完整的冒烟验收流程见 [两条流水线冒烟验收](docs/SMOKE.md)。

### 单家公司推荐

```bash
uv run xft recommend \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"
```

### 批量推荐

准备一个公司名单文件，例如 `company.txt`，每行一个企业名称。

```bash
uv run xft recommend \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --with-web-evidence \
  --batch-id batch-001
```

### 单独准备 Web 缓存

这一步只搜索、抓取、抽取并缓存 Web 证据，可以先不导入 DuckDB：

```bash
uv run xft web enrich --no-etl "企业名称"
```

### 把 Web 缓存导入 DuckDB

```bash
uv run xft web import \
  --input data/web \
  --warehouse cache/company_warehouse.duckdb \
  --rebuild
```

### 重新使用已有 Web 缓存生成推荐

```bash
uv run xft recommend --with-web-evidence "企业名称"
```

### 校验场景配置

```bash
uv run xft scenario validate config/scenarios/sales_recommendation
```

### 查看场景最终解析结果

```bash
uv run xft scenario inspect config/scenarios/sales_recommendation
```

### 查看已有推荐结果汇总

```bash
uv run xft runs inspect --runs-dir recommendation_runs
uv run xft runs inspect --runs-dir recommendation_runs --output recommendation_runs_summary.csv
```

### 运行企业尽调报告

如果要运行旧尽调场景，可以使用：

```bash
uv run xft diligence "企业名称"
uv run xft diligence "企业名称" --dry-run
uv run xft diligence --batch company.txt
```

## 3. 怎么配置参数

业务人员优先修改 `config/scenarios/` 下的场景配置。

推荐默认场景在：

```text
config/scenarios/sales_recommendation/
```

### 3.1 先理解三层配置

推荐配置分三层。日常业务调优优先从第一层开始，只有发现数据或证据不够时，再往下看。

| 层级 | 主要文件 | 解决什么问题 | 业务人员常用程度 |
|------|----------|--------------|------------------|
| 业务结果层 | `business_modules.yaml` | 最终推荐哪个模块、命中哪些标签、输出哪些营销点和 KYC 问题 | 最高 |
| 内部评分层 | `products.yaml`、`scoring_policy.yaml` | 内部候选产品如何打分、排序、兜底 | 中 |
| 证据与数据层 | `analysis_dimensions.yaml`、`evidence_policy.yaml`、`web_search.yaml`、`web_extract_llm.yaml` | 从哪些字段和 Web 证据判断企业特征 | 中 |

通俗说：

```text
business_modules.yaml 决定“业务上怎么说”
products.yaml 决定“内部怎么排序”
analysis_dimensions.yaml / web_search.yaml 决定“系统看哪些证据”
```

### 3.2 配置文件速查

常用配置文件如下：

| 想调整什么 | 修改哪个文件 |
|------------|--------------|
| 最终业务结果、标签、指标、营销点、KYC 问题 | `business_modules.yaml` |
| 内部推荐哪些产品、产品权重、命中规则 | `products.yaml` |
| 分析哪些维度、读取哪些字段、Web 搜索关键词 | `analysis_dimensions.yaml` |
| 评分加减分策略 | `scoring_policy.yaml` |
| 证据可信度、冲突处理、是否跳过 Web | `evidence_policy.yaml` |
| Web 搜索 provider、页数、缓存策略 | `web_search.yaml` |
| Web 证据抽取使用哪个模型 | `web_extract_llm.yaml` |
| LLM 提示词 | `prompts/*.md` |
| 场景入口和文件路径 | `scenario.yaml` |

### 3.3 场景入口：`scenario.yaml`

`scenario.yaml` 用来声明这个场景要使用哪些配置文件。

示例：

```yaml
version: "1.0"
id: sales_recommendation
name: 销售产品推荐

description: 面向企业软件销售线索的产品模块推荐场景

products_config: products.yaml
dimensions_config: analysis_dimensions.yaml
web_search_config: web_search.yaml
web_extract_llm_config: web_extract_llm.yaml
scoring_policy_config: scoring_policy.yaml
evidence_policy_config: evidence_policy.yaml
business_modules_config: business_modules.yaml

prompts:
  match_system: prompts/match_system.md
  recommend_system: prompts/recommend_system.md
  web_extract_system: prompts/extract_evidence_system.md
```

运行时通过 `--scenario` 指向这个目录即可：

```bash
uv run xft recommend --scenario config/scenarios/sales_recommendation "企业名称"
```

### 3.4 推荐的调优流程

不要一上来就改很多文件。建议按这个顺序来：

```text
先跑基线 → 看结果哪里不符合预期 → 只改一个配置点 → 校验配置 → 重跑同一批企业 → 对比结果
```

推荐命令：

```bash
# 1. 先确认配置能解析
uv run xft scenario validate config/scenarios/sales_recommendation

# 2. 查看最终生效配置，尤其是继承和 patch 后的结果
uv run xft scenario inspect config/scenarios/sales_recommendation

# 3. 用本地数据和规则兜底跑一家公司，适合快速看规则是否生效
uv run xft recommend --no-llm \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"

# 4. 用一批企业做校准，适合看整体命中率和错配案例
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --limit 10
```

如果有业务标注文件，优先带上 `--labels`：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

### 3.5 常见调优目标

| 业务现象 | 优先改哪里 | 不建议先改哪里 |
|----------|------------|----------------|
| `result.json` 推荐模块不符合业务预期 | `business_modules.yaml` 的标签、指标、接受度规则 | Python 代码 |
| 命中了不该命中的业务标签 | `business_modules.yaml` 的 `min_matched_indicators`、`standard`、`evidence_hints` | Web provider |
| 销售话术或 KYC 问题不合适 | `business_modules.yaml` 的 `marketing_points` | Prompt |
| 某个产品内部评分经常过高 | `products.yaml` 的 `base_score`、`positive_rules`、`negative_rules` | Prompt |
| 某个产品内部评分经常过低 | `products.yaml` 增加加分规则或提高权重 | 全局评分策略 |
| 报告缺少某类判断依据 | `analysis_dimensions.yaml` 增加维度字段、缺失证据或搜索词 | 产品权重 |
| 本地数据够多但仍频繁搜索 Web | `evidence_policy.yaml` 的 Web 跳过阈值 | Web provider |
| Web 结果噪声太多 | `analysis_dimensions.yaml` 的搜索词、`web_search.yaml` 的 provider/页数 | 产品规则 |
| Web 证据抽取不符合业务口径 | `prompts/extract_evidence_system.md` | 评分策略 |
| 内部整体分数偏高或偏低 | `scoring_policy.yaml` | 单个产品规则 |
| 想做一个新行业/新客群场景 | 新建 `config/scenarios/<新场景>/scenario.yaml`，用 `extends` + `patches` | 复制整份代码 |

### 3.6 调业务结果：`business_modules.yaml`

`business_modules.yaml` 是业务人员最重要的配置文件。它决定最终业务版 `result.json`。

它的结构是：

```text
模块 module
  → 业务标签 label
    → 判断指标 indicator
      → 判断方式 evaluator: rule / llm / hybrid
  → 标签命中后的营销点 marketing_points
```

当前销售推荐场景已覆盖 7 个业务模块：

| `module_id` | 模块 |
|-------------|------|
| `attendance` | 假勤管理 |
| `travel_reimbursement` | 差旅报销 |
| `corporate_payment` | 对公报账 |
| `personal_tax` | 个税管理 |
| `daily_reimbursement` | 日常报销 |
| `input_invoice` | 进项发票 |
| `output_invoice` | 销项发票 |

一个最小示例：

```yaml
modules:
  - module_id: daily_reimbursement
    module_name: 日常报销
    labels:
      - label_id: tech_attribute
        label_name: 科技属性
        min_matched_indicators: 1
        indicators:
          - indicator_id: ip_assets
            indicator_name: 知识产权数量多
            evaluator: rule
            standard: 存在专利、软著或其他知识产权资产。
            rule:
              source_field: ip_counts.patent
              op: ">"
              value: 0
          - indicator_id: tech_certification
            indicator_name: 科技企业-科技资质认证
            evaluator: hybrid
            merge_policy: rule_first
            standard: 企业具备高新技术企业、专精特新、科技型中小企业等资质。
            rule:
              source_field: labels
              op: contains_any
              value:
                - 高新技术企业
                - 专精特新
                - 科技型中小企业
            evidence_hints:
              - 高新技术企业
              - 专精特新
```

字段含义：

| 字段 | 含义 |
|------|------|
| `module_id` | 模块稳定 ID，建议和 `products.yaml` 里的模块 ID 对齐 |
| `label_id` | 业务属性标签 ID，例如 `tech_attribute` |
| `min_matched_indicators` | 一个标签至少命中几个指标才算“满足” |
| `indicator_id` | 指标 ID，例如 `ip_assets` |
| `evaluator` | 判断方式，`rule` 表示确定性规则，`llm` 表示 LLM 结合证据推理，`hybrid` 表示 rule + LLM 协同 |
| `standard` | 判断标准，会进入最终 `result.json` 的 `QuantitativeStandard` |
| `evidence_hints` | `--no-llm` 兜底或 LLM 判断时使用的关键词提示 |
| `marketing_points` | 标签命中后输出的推荐理由、销售规则和 KYC 问题 |

`rule` 适合明确字段：

```yaml
rule:
  source_field: ip_counts.patent
  op: ">"
  value: 0
```

`llm` 适合业务语义判断：

```yaml
evaluator: llm
prompt: |
  请判断企业是否存在经销商维护、区域销售、渠道维护或业务员外勤销售特征。
```

`hybrid` 适合“有明确字段线索，但需要 LLM 兜底或确认”的判断：

```yaml
evaluator: hybrid
merge_policy: rule_first
rule:
  source_field: labels
  op: contains_any
  value: [高新技术企业, 专精特新]
prompt: |
  请结合企业画像和维度证据，判断企业是否具备科技资质。
```

支持三种合并策略：

| `merge_policy` | 含义 |
|----------------|------|
| `rule_first` | rule 命中则直接 matched，不调用 LLM；rule 未命中再交给 LLM |
| `llm_confirm` | rule 给出候选信号，LLM 负责确认或降级 |
| `require_both` | rule 和 LLM 都命中才算 matched |

运行 `--no-llm` 时，LLM 指标不会调用模型，会使用 `evidence_hints` 在本地画像里做保守兜底判断。

最终输出关系：

| 输出字段 | 来源 |
|----------|------|
| `Module` | 选中的 `module_name` |
| `LabelResult` | 命中的指标判断结果 |
| `Acceptance` | 命中的业务属性标签 |
| `MarketingPoint` | 命中标签对应的 `marketing_points` |
| `AttributesNumber` | 当前模块下满足的标签数量，运行时计算 |
| `IndicatorsNumber` | 当前模块下满足的指标数量，运行时计算 |
| `AcceptanceResult` | `acceptance_policy` 根据标签数量计算 |

业务人员最常改的是：

- 新增或删除某个标签下的 `indicators`。
- 把某个指标从 `rule` 改成 `llm`。
- 修改 `min_matched_indicators`，让标签更宽松或更严格。
- 修改 `standard`，让判断标准更贴近业务。
- 修改 `marketing_points`，让推荐理由和 KYC 问题更像销售话术。

### 3.7 调内部产品评分：`products.yaml`

当你觉得“推荐了不该推荐的产品”或“该推荐的产品没上来”，优先改 `products.yaml`。

注意：`products.yaml` 影响 `internal_result.json` 的内部评分和兜底排序；`result.json` 的业务标签和营销点主要由 `business_modules.yaml` 决定。两个文件必须通过同一个 `module_id` 一一对齐。

也就是说，如果 `business_modules.yaml` 里有 7 个业务模块，`products.yaml` 也应该是同样 7 个 `module_id`。`uv run xft scenario validate ...` 会检查这个一致性，避免旧产品配置残留后在“产品匹配”阶段冒出额外候选。

一个产品模块最常调的是：

| 字段 | 业务含义 | 调整建议 |
|------|----------|----------|
| `base_score` | 产品基础分 | 产品太容易上榜就降低，太难上榜就提高 |
| `priority` | 同分排序 | 只影响同分或接近分数时的顺序 |
| `target_needs` | 产品关注的分析维度 | 产品依赖哪些维度，就填哪些维度 ID |
| `match_rule` | 报告里的业务解释 | 用业务人员能看懂的话描述推荐逻辑 |
| `positive_rules` | 命中后加分 | 用来表达“出现什么信号就更适合” |
| `negative_rules` | 扣分 | 用来表达“缺少什么信息或出现什么弱信号就谨慎” |
| `exclusion_rules` | 排除或强压分 | 用来表达“出现什么情况基本不适合” |

示例：员工规模较大时，提高“人力资源与考勤管理”的推荐分：

```yaml
positive_rules:
  - id: employee_scale_signal
    source_field: employee_count
    op: ">="
    value: 200
    weight: 10
    reason: 员工规模较大，通常存在考勤排班、组织人事与薪酬绩效管理需求。
```

示例：缺少倒班制度信息时，对“人力资源与考勤管理”谨慎扣分：

```yaml
negative_rules:
  - id: missing_shift_policy
    missing_evidence: 倒班制度
    penalty: 5
    reason: 缺少倒班制度信息，影响考勤排班方案判断。
```

调优建议：

- `weight` 或 `penalty` 一次不要改太大，建议每次调整 `3-8` 分。
- 如果一个产品总是排第一，先看它是不是 `base_score` 太高。
- 如果一个产品完全上不来，先看它的 `target_needs` 是否能被维度支持。
- `reason` 会进入解释链路，写给业务人员看，不要写成技术字段说明。

### 3.8 调分析维度：`analysis_dimensions.yaml`

当你觉得“系统没有看懂企业的某类特征”，改 `analysis_dimensions.yaml`。

一个维度最常调的是：

| 字段 | 业务含义 | 调整建议 |
|------|----------|----------|
| `local_fields` | 优先读取哪些本地企业画像字段 | 本地 JSON 已经有的字段，优先加在这里 |
| `evidence_templates` | 本地字段如何显示成证据 | label 要写成人能读懂的名字 |
| `insufficient_evidence` | 缺少哪些信息要提示 | 用来告诉报告“还缺什么才能判断更准” |
| `support_rules` | 本地字段满足条件时如何形成判断 | 适合稳定、明确的业务规则 |
| `web_search_queries` | 本地不足时搜什么 | 搜索词越具体，噪声越少 |
| `analysis_prompt` | LLM 分析维度时的要求 | 用来约束不要编造、不要过度推断 |
| `evidence_policy` | 该维度证据强弱口径 | 写清哪些是强证据，哪些只是弱线索 |

示例：把“招投标数量”作为供应链复杂度线索：

```yaml
support_rules:
  - field: bidding_total
    op: ">"
    value: 0
    claim: 存在招投标记录，可作为项目型采购或销售管理复杂度线索。
    confidence: 低
```

示例：为业务产品维度增加 Web 搜索词：

```yaml
web_search_queries:
  - "{company_name} 官网 产品"
  - "{company_name} 主营产品"
  - "{company_name} 客户案例"
```

调优建议：

- 有明确本地字段时，先加 `local_fields` 和 `support_rules`，不要一开始依赖 Web。
- 搜索词要带 `{company_name}`，否则容易搜到行业泛信息。
- `confidence` 不要随便写高。行业、规模、经营范围通常是弱线索；官网产品、客户案例、公告通常更强。
- `insufficient_evidence` 很重要，它能让报告明确“为什么还不能高置信判断”。

### 3.9 调评分口径：`scoring_policy.yaml`

当你觉得“不是某个产品错了，而是整体分数偏高/偏低”，再改 `scoring_policy.yaml`。

常见配置含义：

| 配置 | 含义 |
|------|------|
| `dimension_support.supported_score` | 维度被支持时给产品的基础加分 |
| `dimension_support.partial_score` | 维度部分支持时的加分 |
| `evidence_support.per_item` | 每条证据给多少分 |
| `evidence_support.cap` | 证据加分上限 |
| `web_support.confirmation_per_item` | Web 佐证每条加多少分 |
| `web_support.supplement_per_item` | Web 补充证据每条加多少分 |
| `penalties.conflict_per_item` | 每条冲突证据扣多少分 |
| `penalties.missing_evidence_cap` | 缺失证据最多扣多少分 |
| `exclusion.score_cap` | 触发排除规则后最高只能到多少分 |

调优建议：

- 如果所有产品分数都偏高，降低 `dimension_support` 或 `evidence_support`。
- 如果 Web 证据影响过大，降低 `web_support`。
- 如果存在冲突还被高分推荐，提高 `penalties.conflict_per_item`。
- 如果缺失信息导致扣分过重，降低 `missing_evidence_cap`。

### 3.10 调证据使用策略：`evidence_policy.yaml`

当你关注“证据够不够、是否要搜 Web、冲突怎么处理”时，改 `evidence_policy.yaml`。

常见配置含义：

| 配置 | 含义 |
|------|------|
| `web_planning.supported_facts_to_skip_web` | 本地已有多少条事实后跳过 Web 搜索 |
| `dimension_analysis.supported_facts_threshold` | 一个维度达到 supported 需要多少条事实 |
| `resolver.source_priority` | 多来源证据冲突时的来源优先级 |
| `resolver.authority_boost` | 高权威来源如何提升置信度 |
| `resolver.quality_score` | 不同关系证据如何计算质量分 |
| `recommender.supported_quality_threshold` | 质量分达到多少才算维度支持 |

调优建议：

- 如果希望更少搜索 Web，降低 `supported_facts_to_skip_web`。当前含义是“本地事实数达到该值就跳过 Web”，所以阈值越低，越容易跳过 Web。
- 如果本地弱证据太容易让维度变成 supported，提高 `supported_quality_threshold`。
- 如果 Web 和本地冲突，默认以本地 JSON 为准。不要轻易把 `web` 的优先级排到 `local_json` 前面。
- `manual` 适合人工确认后的证据，优先级可以高于普通规则和 Web。

### 3.11 调 Web 搜索：`web_search.yaml`

当你觉得“Web 搜不到、搜太多、噪声太大、抓取太慢”，改 `web_search.yaml` 和维度里的 `web_search_queries`。

常见配置含义：

| 配置 | 含义 |
|------|------|
| `default_providers` | 默认使用哪些搜索 provider |
| `providers.*.enabled` | 是否启用某个 provider |
| `execution.max_queries_per_dimension` | 每个维度最多跑多少个搜索词 |
| `execution.max_results_per_query` | 每个搜索词最多取多少条结果 |
| `execution.fetch_pages` | 是否抓取网页正文 |
| `fetch.blocked_domains` | 不抓取哪些域名 |
| `fetch.max_full_text_chars` | 每个网页最多保留多少正文字符 |

调优建议：

- 搜索噪声大，优先改 `analysis_dimensions.yaml` 的 `web_search_queries`，让搜索词更具体。
- 搜索成本高，降低 `max_queries_per_dimension` 或 `max_results_per_query`。
- 抓取慢，降低 `fetch.concurrency` 或关闭 `fetch_pages`。
- 某些站点质量差或反爬严重，加入 `blocked_domains`。

刷新缓存命令：

```bash
uv run xft web enrich --refresh-search "企业名称"       # 仅重新搜索
uv run xft web enrich --refresh-fetch "企业名称"        # 仅重新抓取网页
uv run xft web enrich --refresh-extraction "企业名称"   # 仅重新抽取证据
```

### 3.12 调 LLM 和 Prompt

当你觉得“推荐理由写得不对、Web 证据抽取口径不对、模型过度推断”，改 `prompts/*.md`。

常见文件：

| 文件 | 用途 |
|------|------|
| `prompts/match_system.md` | 产品匹配时的系统提示词 |
| `prompts/recommend_system.md` | 生成推荐理由时的系统提示词 |
| `prompts/extract_evidence_system.md` | 从 Web 内容抽取证据时的系统提示词 |
| `web_extract_llm.yaml` | Web 证据抽取模型、温度、超时和输入长度 |

调优建议：

- 如果模型编造信息，在 prompt 中明确“只能基于证据，不得补充未知事实”。
- 如果 Web 抽取混入无关公司，在 `extract_evidence_system.md` 中强化“必须确认目标企业名称”。
- 如果输出太发散，保持 `temperature: 0`。
- 修改 Web 抽取 prompt 后，使用 `--refresh-extraction` 重新抽取。
- 如果外部 LLM 失败但不知道原因，运行时加 `--llm-debug`，终端会显示失败阶段、异常类型和兜底路径。
- 如果业务标签判断太慢，可以适当提高 `--llm-concurrency`；如果接口限流或不稳定，就调低到 `1` 或 `2`。

### 3.13 新增一个业务场景

如果只是新客群、新行业、新销售策略，不建议复制整套配置。优先用 `extends` 继承已有场景，只写差异。

例如 `config/scenarios/bank_marketing/scenario.yaml` 继承销售推荐场景，只调整 CRM 产品：

```yaml
extends: ../sales_recommendation
id: bank_marketing
name: 银行业营销场景

patches:
  products:
    - module_id: crm_channel
      set:
        base_score: 55
        match_rule: 银行高质量客户、跨境结算活跃或渠道经营线索明显的企业，优先考虑客户与渠道管理。
      append_positive_rules:
        - id: bank_high_quality_customer
          source_field: bank_flags.high_quality_customer
          op: "=="
          value: true
          weight: 12
          reason: 银行高质量客户标签提示金融服务匹配度更高。
```

新增或修改场景后，先校验：

```bash
uv run xft scenario validate config/scenarios/bank_marketing
uv run xft scenario inspect config/scenarios/bank_marketing
```

### 3.14 如何判断调优是否有效

单家公司适合看解释是否合理：

```bash
uv run xft recommend --no-llm \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"
```

一批企业适合看整体命中率：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

建议每次调优记录：

| 记录项 | 示例 |
|--------|------|
| 改了什么 | `crm_channel.base_score 46 -> 50` |
| 为什么改 | CRM 在渠道型企业中排名偏低 |
| 验证哪些企业 | `company.txt` 前 10 家 |
| 结果如何 | Top1 命中率、可接受命中率、错配案例 |
| 是否保留 | 保留 / 回滚 / 继续调 |

## 4. 配置 API Key

项目会读取 `.env` 中的环境变量。可以参考 `.env.example` 创建本地 `.env`。

常见配置包括：

```bash
MINIMAX_API_KEY=你的密钥
METASO_API_KEY=你的密钥
OPENAI_API_KEY=你的密钥
```

实际需要哪些密钥，取决于你在 `web_search.yaml` 和 `web_extract_llm.yaml` 中启用了哪些 provider 和模型。

如果只跑离线推荐，可以先不配置 Web 和 LLM 密钥：

```bash
uv run xft recommend --no-llm "企业名称"
```

## 5. Docker 使用方法

项目的 Docker 入口已经统一为 `xft`。构建镜像：

```bash
docker build -t xft:latest .
```

查看容器内可用命令：

```bash
docker run --rm xft:latest --help
```

挂载本地数据和输出目录后构建数据库：

```bash
docker run --rm \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/cache:/app/cache" \
  -v "$PWD/recommendation_runs:/app/recommendation_runs" \
  xft:latest warehouse build --input data --output cache/company_warehouse.duckdb
```

运行推荐：

```bash
docker run --rm \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/cache:/app/cache" \
  -v "$PWD/recommendation_runs:/app/recommendation_runs" \
  xft:latest recommend --no-llm "企业名称"
```

如果要使用本地修改过的配置，可以额外挂载 `config/`：

```bash
docker run --rm \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/cache:/app/cache" \
  -v "$PWD/config:/app/config:ro" \
  -v "$PWD/recommendation_runs:/app/recommendation_runs" \
  xft:latest recommend --scenario config/scenarios/sales_recommendation "企业名称"
```

也可以使用 `docker compose`：

```bash
docker compose build
docker compose run --rm xft --help
docker compose run --rm xft warehouse build --input data --output cache/company_warehouse.duckdb
docker compose run --rm xft recommend --no-llm "企业名称"
```

## 6. 常见问题

### 找不到企业怎么办？

先确认企业 JSON 已经放在 `data/`，并且已经重建数据库：

```bash
uv run xft warehouse build --input data --output cache/company_warehouse.duckdb
```

### 修改了配置，为什么结果没变？

建议确认运行时使用了正确场景：

```bash
uv run xft scenario inspect config/scenarios/sales_recommendation
```

如果改的是 Web 搜索或抽取 prompt，可能命中了缓存。可以选择刷新：

```bash
uv run xft web enrich --refresh-extraction "企业名称"
uv run xft recommend --with-web --refresh-web "企业名称"
```

### Web 已经搜索过，能不能不重复抓？

可以。默认会尽量复用缓存。只有显式加 `--refresh-web`、`--refresh-search`、`--refresh-fetch` 或 `--refresh-extraction` 时，才会强制刷新对应环节。
