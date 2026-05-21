# XFT 企业推荐工具

XFT 根据本地企业画像、业务规则、LLM 和可选的公开 Web 证据，为销售/业务人员生成企业适配的产品模块推荐结果。

业务人员通常只需要关注三件事：

1. 企业画像库是否已构建到 DuckDB。
2. 推荐场景配置是否正确。
3. `result.json` 和 `report.md` 是否能解释推荐结论。

## 推荐流程

```mermaid
flowchart LR
    data["data/ 企业 JSON"] --> warehouse["DuckDB 企业画像库"]
    warehouse --> gather["读取企业画像和本地证据"]
    gather --> recommend["指标判断 rule / llm / hybrid / llm_web"]
    recommend --> web["需要时按指标 Web 补证"]
    web --> recommend
    recommend --> output["result.json + report.md"]
```

当前默认场景：

```text
config/recommender/xft
```

当前推荐主链路：

```text
data_gather -> recommend -> save
```

`--with-web` 开启后，Web 不再先把所有指标搜一遍，而是在每个指标计算到证据不足、规则未命中或 `llm_web` 必须取公开证据时才搜索。

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 准备企业画像库

把企业 JSON 目录放到 `data/`，目录名格式通常是：

```text
统一社会信用代码_企业名称/
```

构建 DuckDB：

```bash
uv run xft warehouse build --input data --output cache/company_warehouse.duckdb
```

### 3. 检查推荐配置

```bash
uv run xft scenario validate config/recommender/xft
```

正常情况下会看到类似结果：

```json
{
  "scenario_id": "sales_recommendation",
  "scenario_name": "销售产品推荐",
  "root": "config/recommender/xft",
  "web_enabled": true,
  "modules": 7
}
```

### 4. 跑一家公司

离线模式，不调用 LLM、不搜索 Web，适合快速检查本地规则：

```bash
uv run xft recommend --no-llm "企业名称"
```

启用 LLM：

```bash
uv run xft recommend "企业名称"
```

启用 Web 补证：

```bash
uv run xft recommend --with-web "企业名称"
```

刷新 Web 缓存：

```bash
uv run xft recommend --with-web --web-refresh "企业名称"
```

指定 Web provider：

```bash
uv run xft recommend --with-web --web-provider minimax_search "企业名称"
```

只测试一个模块：

```bash
uv run xft recommend --module 个税管理 --with-web "企业名称"
```

同时测试多个模块：

```bash
uv run xft recommend --module 个税管理 --module 差旅报销 "企业名称"
```

只测试单个指标：

```bash
uv run xft recommend --module 个税管理 --label 多分支机构_集团化制造企业 --indicator 招聘信息 --with-web "企业名称"
```

审计当前场景配置：

```bash
uv run xft scenario audit config/recommender/xft
```

## 常用参数

| 参数 | 用途 | 什么时候用 |
| --- | --- | --- |
| `--warehouse` | 指定 DuckDB 文件，默认 `cache/company_warehouse.duckdb` | 有多份企业画像库时 |
| `--scenario` | 指定场景目录，默认 `config/recommender/xft` | 跑非默认场景时 |
| `--module` | 只评估指定 `module_id`，可重复传入 | 调试单个模块的规则、LLM、Web 搜索词时 |
| `--label` | 只评估指定 `label_id`，可重复传入，必须同时指定 `--module` | 调试模块下某一类业务属性时 |
| `--indicator` | 只评估指定 `indicator_id`，可重复传入，必须同时指定 `--module` 和 `--label` | 精调单个指标、搜索词、prompt 时 |
| `--output-dir` | 指定输出目录，默认来自 `scenario.yaml` | 临时试跑或隔离结果时 |
| `--no-llm` | 关闭 LLM，只跑规则和兜底判断 | 快速冒烟、排查规则配置时 |
| `--with-web` | 启用指标级 Web 补证 | 需要公开网页证据时 |
| `--web-refresh` | 忽略已有 Web 查询缓存，重新搜索 | 调整查询词或怀疑缓存过旧时 |
| `--web-provider` | 指定 Web 搜索 provider，逗号分隔 | 对比 `minimax_search` / `metaso_search` 时 |
| `--llm-debug` | 打印 LLM 调用耗时、错误和响应预览 | 调试 prompt、证据不足、LLM 失败时 |
| `--llm-concurrency` | 设置 LLM 并发数，默认 4 | 批量跑且需要控制成本/限流时 |
| `--company-list` | 批量读取企业名单文件 | 批量推荐时 |
| `--limit` | 只跑名单前 N 家 | 小样本验证时 |
| `--skip-existing` | 已有 `result.json` 的企业跳过 | 断点续跑批量任务时 |

批量推荐示例：

```bash
uv run xft recommend \
  --company-list company.txt \
  --with-web \
  --limit 10
```

三层调试示例：

```bash
# 只看一个模块
uv run xft recommend --module 假勤管理 "企业名称"

# 只看模块下某个标签，保留该标签下所有指标
uv run xft recommend --module 假勤管理 --label 科技属性 "企业名称"

# 只看某个指标，必须给出完整 module + label 语境
uv run xft recommend --module 假勤管理 --label 科技属性 --indicator 细分行业 "企业名称"
```

## 运行结果怎么看

每次推荐会写入：

```text
outputs/recommender/xft/<run_id>/
```

核心文件：

| 文件 | 业务用途 |
| --- | --- |
| `result.json` | 最终推荐交付结果，业务系统优先读取这个 |
| `report.md` | 人类可读报告，适合人工检查 |
| `logs/<run_id>.log` | 人类可读调试日志，按模块/标签/指标展开每个决策点 |
| `label_result.json` | 模块、标签、指标的完整判断明细 |
| `indicator_evidence.json` | 每个指标使用的本地证据和 Web 证据 |
| `profile.json` | 本次读取到的企业画像 |
| `decision_trace.json` | 规则、Web、LLM 的决策过程 |
| `llm_calls.jsonl` | LLM 调用记录 |
| `llm_metrics.json` | LLM 调用统计 |
| `web_queries.jsonl` | Web 查询记录，仅启用 `--with-web` 时生成 |
| `web_results.jsonl` | Web 搜索结果，仅启用 `--with-web` 时生成 |
| `web_trace.json` | Web 补证 trace |
| `scenario_resolved.json` | 本次运行解析后的场景配置 |
| `config_manifest.json` | 本次运行使用的配置文件和哈希 |

判断一次推荐是否可用，建议按顺序看：

1. `result.json` 的 `Module`、`AcceptanceResult`、`Conclusion`。
2. `logs/<run_id>.log` 是否能解释每个指标为什么命中、未命中或调用 Web/LLM。
3. `report.md` 是否能解释推荐理由。
4. `indicator_evidence.json` 是否有足够证据支撑命中指标。
5. 如启用 LLM，检查 `llm_metrics.json` 是否有失败调用。
6. 如启用 Web，检查 `web_trace.json` 中查询词和结果是否与指标相关。

## 配置文件怎么改

默认场景目录：

```text
config/recommender/xft/
  scenario.yaml
  modules.yaml
  modules.d/
    个税管理.yaml
    假勤管理.yaml
    对公报账.yaml
    差旅报销.yaml
    日常报销.yaml
    进项发票.yaml
    销项发票.yaml
  web_search.yaml
```

### `scenario.yaml`

声明场景入口、输出目录和 Web 缓存目录：

```yaml
version: "1.0"
id: sales_recommendation
name: 销售产品推荐
description: 面向企业软件销售线索的产品模块推荐场景

web_search_config: web_search.yaml
modules_config: modules.yaml

output_dir: ../../../outputs/recommender/xft
web_cache_root: ../../../data/web/recommender/xft
```

常调参数：

| 字段 | 含义 |
| --- | --- |
| `modules_config` | 指向主模块配置，默认 `modules.yaml` |
| `web_search_config` | 指向 Web provider 配置，默认 `web_search.yaml` |
| `output_dir` | 推荐结果输出目录 |
| `web_cache_root` | Web 查询缓存目录 |

### `modules.yaml`

配置全局分数、接受策略和模块目录：

```yaml
version: "1.0"
scenario: sales_recommendation
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
acceptance_policy:
  levels:
    - result: 高
      min_matched_labels: 3
      conclusion: 企业满足{attributes_number}个属性标签及{indicators_number}个指标，接受度为高。
    - result: 中高
      min_matched_labels: 2
      conclusion: 企业满足{attributes_number}个属性标签及{indicators_number}个指标，接受度为中高。
    - result: 低
      min_matched_labels: 0
      conclusion: 企业满足{attributes_number}个属性标签及{indicators_number}个指标，接受度为低。
modules_dir: modules.d
```

常调参数：

| 字段 | 调整效果 |
| --- | --- |
| `indicator_scores` | 控制单个指标对结果的贡献 |
| `label_scores` | 控制标签命中对模块分的贡献 |
| `acceptance_policy.levels` | 控制“高 / 中高 / 低”的门槛和结论文案 |
| `modules_dir` | 指定模块文件目录 |

### `company_profile` 是什么

`company_profile` 是推荐流水线读取的企业画像主表，由 `xft warehouse build` 从 `data/` 下的企业 JSON 聚合生成。它是 `rule.source_field` 的主要来源：当指标里写 `rule.source_field: industry`、`rule.source_field: labels`、`rule.source_field: ip_counts.patent` 时，系统就是从当前企业的 `company_profile` 里取值判断。

常用字段：

| 字段 | 类型 / 示例 | 适合判断什么 |
| --- | --- | --- |
| `company_name` / `credit_code` | 企业名称 / 统一社会信用代码 | 运行定位、Web 结果归属校验 |
| `industry` / `industry_big` / `industry_mid` / `industry_small` | 制造业、软件和信息技术服务业等 | 行业、细分行业、制造业属性 |
| `business_scope` | 经营范围文本 | 主营业务、产品、服务类型 |
| `employee_count` | 员工人数 | 企业规模、用工规模 |
| `registered_capital` / `registered_location` / `province` / `county` | 注册资本、地区 | 区域、规模、注册地 |
| `labels` | JSON 列表，如高新技术企业、专精特新 | 企业标签、科技资质、银行标签 |
| `ip_counts.patent` / `ip_counts.software` | 专利数、软著数 | 知识产权、研发属性 |
| `recent_recruitment_titles` / `recruitment_count` | 近期招聘标题、招聘数量 | 岗位需求、组织能力、招聘信号 |
| `branch_count` | 分支机构数量 | 多区域经营、集团化管理 |
| `qualification_count` | 资质数量 | 资质丰富度、科技/行业认证 |
| `outbound_investment_count` | 对外投资数量 | 多法人主体、集团化经营 |
| `cross_border_flags` | 跨境相关标记 | 出口、跨境、海外业务线索 |
| `profile_completeness` | 0-1 | 判断画像是否足够完整 |

字段规则示例：

```yaml
evaluator: rule
standard: 企业属于制造业
rule:
  source_field: industry
  op: contains
  value: 制造
```

嵌套字段可以用点号：

```yaml
evaluator: rule
standard: 企业存在专利
rule:
  source_field: ip_counts.patent
  op: ">"
  value: 0
```

如果要读取明细表，不要写 `rule.source_field`，而是写 `data_sources`。当前常用明细表包括 `recruitments`、`branches`、`qualifications`、`outbound_investments`、`key_personnel`。

### `modules.d/*.yaml`

一个模块一个文件。新增模块就是新增 YAML 文件；删除模块就是删除文件。

模块示例：

```yaml
module_id: 日常报销
module_name: 日常报销
priority: 50
base_score: 0
labels:
  - label_id: 销售属性
    label_name: 销售属性
    min_matched_indicators: 1
    indicators:
      - indicator_id: 渠道销售岗位
        indicator_name: 渠道销售岗位
        evaluator: rule
        standard: 招聘标题包含销售或渠道
        data_sources:
          - type: table
            table: recruitments
            field: title
            op: text_contains
            keywords:
              - 销售
              - 渠道
```

每个指标最重要的字段：

| 字段 | 用途 |
| --- | --- |
| `evaluator` | 判断方式：`rule`、`llm`、`hybrid`、`llm_web` |
| `standard` | 业务判断标准 |
| `rule` | 直接读取企业画像字段进行判断 |
| `data_sources` | 从 DuckDB 明细表取本地证据 |
| `prompt` | LLM 判断时的任务说明 |
| `evidence_hints` | LLM 关注的证据线索 |
| `web_search` | 指标级 Web 补证策略 |

### evaluator 怎么选

| 你手里的证据形态 | 推荐 evaluator | 典型例子 | Web 角色 |
| --- | --- | --- | --- |
| `company_profile` 字段或明细表能直接判断 | `rule` | 行业包含制造、标签包含高新技术企业、分支机构数 > 0、招聘标题包含关键词 | 可选；通常只在规则未命中时补线索，最多补到 `possible` |
| 有本地证据，但要判断语义、归类或业务含义 | `llm` | 根据经营范围判断是否属于科技制造，根据多段证据判断是否有集团化管理需求 | 可选；常用 `when: insufficient` |
| 先用规则抓硬信号，规则不够时再让 LLM 判断 | `hybrid` | 标签命中则直接通过；未命中时结合资质、经营范围、Web 证据判断 | 推荐；适合调试阶段的大多数复杂指标 |
| 本地没有可靠数据，只能靠公开网页 | `llm_web` | 官网/新闻披露研发投入、海外客户、验厂、具体业务模式 | 必须；没有实际 Web 证据时返回 `unknown` |

推荐顺序：

1. 能 `rule` 就不要 `llm`。
2. 有硬规则但还需要解释，优先 `hybrid`。
3. 本地证据足够但需要语义判断，才用 `llm`。
4. 只有公开网页才可能判断，才用 `llm_web`。

快速判断：

```text
能从 company_profile / 明细表直接比较字段？  -> rule
有本地证据，但需要读懂文本含义？              -> llm
有硬规则可先挡一层，剩下交给 LLM？            -> hybrid
本地数据没有，只能公开搜索？                  -> llm_web
```

### 本地证据怎么配

当前表级 `data_sources` 支持：

```text
recruitments
branches
qualifications
outbound_investments
key_personnel
```

`text_contains` 必须写具体 `keywords`，不要留空：

```yaml
data_sources:
  - type: table
    table: recruitments
    field: title
    op: text_contains
    keywords:
      - 销售
      - 渠道
```

如果只是判断是否存在记录，用 `op: exists`，不要用空关键词模拟存在性。

### Web 补证怎么配

指标级 `web_search` 常用字段：

| 字段 | 可选值 / 含义 |
| --- | --- |
| `when` | `always`、`insufficient`、`rule_not_matched`、`never` |
| `effect` | `llm_evidence`、`evidence_only`、`possible_on_evidence` |
| `fixed_queries` | 固定查询词，支持 `{company_name}` |
| `auto.enabled` | 是否让 LLM 生成少量补充查询 |
| `auto.max_queries` | 自动查询数量上限 |
| `auto.intent` | 自动查询目标 |
| `max_results` | 每个查询最多保留的结果数 |

示例：

```yaml
evaluator: hybrid
merge_policy: rule_first
web_search:
  when: insufficient
  effect: llm_evidence
  fixed_queries:
    - "{company_name} 差旅 报销 制度"
  auto:
    enabled: true
    max_queries: 2
    intent: 判断企业是否有差旅、商旅、报销、费控管理需求
```

查询词要带指标词，不要只写 `{company_name} 官网` 或 `{company_name} 新闻`。

执行时机是 lazy 的：系统先使用本地画像和 DuckDB 证据判断当前指标；只有该指标的 `when` 条件满足时才搜索。常见选择：

- `llm_web` 默认 `when: always`，因为它本来就依赖公开网页。
- `llm` / `hybrid` 常用 `when: insufficient`，本地证据足够时不搜索。
- `rule` 常用 `when: rule_not_matched` + `effect: possible_on_evidence`，规则已命中时不搜索，规则未命中时 Web 线索最多提升为 `possible`。

### `web_search.yaml`

配置 Web provider 和查询上限：

```yaml
version: "1.1"
enabled: true
cache_root: data/web
default_providers:
  - minimax_search

providers:
  minimax_search:
    type: minimax
    enabled: true
    max_results: 5
    timeout_seconds: 30

  metaso_search:
    type: metaso
    enabled: true
    mode: search
    search_size: 3
    timeout_seconds: 30

execution:
  max_results_per_query: 5
```

场景里的 `web_cache_root` 会覆盖这里的 `cache_root`。

## LLM 和 Web Key

`.env` 支持：

```text
LLM_API_KEY=...
LLM_BASE_URL=https://api.minimax.io/v1
LLM_MODEL=MiniMax-M2.7-Highspeed

MINIMAX_API_KEY=...
METASO_API_KEY=...
METASO_ENABLED=true
```

也可以用 SM4 前缀保存 key：

```bash
python -m xft.keys encode <plaintext_key>
```

## 调优建议

### 推荐结果不准

1. 先看 `label_result.json`，确认误命中的模块、标签和指标。
2. 再看 `indicator_evidence.json`，确认证据是否真的支持指标。
3. 如果本地证据误命中，优先调整 `data_sources.keywords` 或 `rule`。
4. 如果 Web 噪声误导，调整对应指标的 `fixed_queries`、`when`、`effect`。
5. 如果接受度过高或过低，调整 `modules.yaml` 的 `acceptance_policy`。

调试单个模块时先加 `--module <module_id>`，缩小输出和 LLM/Web 调用范围。调试某类业务属性时加 `--label <label_id>`；精调某个指标时使用完整三层参数：`--module <module_id> --label <label_id> --indicator <indicator_id>`。确认该模块稳定后，再去掉过滤参数做全场景对比。

每次运行都会生成 `logs/<run_id>.log`。调配置时优先读这个文件，它会先给出调优建议摘要，再按模块、标签、指标展开 Rule、Data sources、Web policy、Web 查询、LLM 调用和最终采纳证据。

### 让 LLM 帮你配置指标

不建议长期手改大段 YAML。调试阶段可以直接用自然语言描述目标，让 LLM 帮你改 `modules.d/*.yaml`、跑审计和单指标验证。

推荐这样提需求：

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

请你选择 evaluator，修改配置，运行 scenario audit，并用：
uv run xft recommend --module 假勤管理 --label 科技属性 --indicator 研发投入 --with-web --llm-debug "企业名称"
做一次验证。
```

LLM 配置时应交付：

1. 说明为什么选 `rule` / `llm` / `hybrid` / `llm_web`。
2. 修改对应 `config/recommender/xft/modules.d/<module>.yaml`。
3. 更新必要文档。
4. 运行 `uv run xft scenario audit config/recommender/xft`。
5. 用 `--module --label --indicator` 跑单指标验证。
6. 汇总命中、未命中、Web 查询词、LLM 判断和下一步调参建议。

### LLM 成本或速度有问题

- 冒烟时用 `--no-llm`。
- 批量时降低 `--llm-concurrency`。
- 能写成 `rule` 的指标不要写成 `llm`。
- `hybrid` 建议使用 `merge_policy: rule_first`，规则命中时可跳过 LLM。

### Web 证据噪声大

- 固定查询词必须包含指标词。
- Web 是按指标缺口触发的；如果查询过多，优先检查哪些指标配置了 `when: always` 或泛化查询词。
- `llm_web` 没有实际 Web 证据时会输出 `unknown`，不会空证据调用 LLM。
- `rule` 配 `effect: possible_on_evidence` 时，Web 证据最多提升为 `possible`，不会直接变成 `matched`。
- 抽查 `web_trace.json`，确认过滤后的结果既属于目标公司，也与指标相关。

## 校准

准备企业名单：

```text
company.txt
```

准备人工标注 CSV：

```csv
company_name,expected_top_module,acceptable_modules,comment
某公司,日常报销,日常报销；差旅报销,人工标注说明
```

运行校准：

```bash
uv run xft calibrate \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

启用 LLM 和 Web：

```bash
uv run xft calibrate \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --with-llm \
  --with-web \
  --limit 10
```

校准结果会输出 top1 命中率、可接受命中率、Web 覆盖率和需要人工复核的样本。

## Docker

构建镜像：

```bash
docker build -t xft-dd .
```

查看帮助：

```bash
docker run --rm xft-dd uv run xft --help
```

挂载数据、缓存和输出目录后运行：

```bash
docker run --rm \
  -v "$PWD/data:/app/data" \
  -v "$PWD/cache:/app/cache" \
  -v "$PWD/outputs:/app/outputs" \
  xft-dd uv run xft recommend --no-llm "企业名称"
```

## 技术文档

- [架构说明](docs/ARCHITECTURE.md)
- [模块调优流程](docs/MODULE_TUNING.md)
- [评分与指标配置](docs/SCORING.md)
- [冒烟验收](docs/SMOKE.md)
- [下一步计划](docs/NEXT.md)
- [技术债](docs/TECH_DEBT.md)
- [DuckDB 数据流](docs/duckdb_data_flow_design.md)
