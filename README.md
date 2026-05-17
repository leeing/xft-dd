# 企业分析与推荐平台

本项目是一个面向业务人员使用的企业分析工具。你把企业数据放进项目，选择一个业务场景，系统会读取本地企业画像，必要时补充 Web 证据，然后生成产品推荐结果和报告。

当前主力场景是“产品模块推荐”：根据企业行业、规模、资质、招投标、知识产权、招聘、风险等信息，判断企业更可能适合哪些产品模块，并解释推荐原因、证据来源和风险点。

更偏技术的架构、数据流、模块边界和后续计划已经移到：

- [架构说明](docs/ARCHITECTURE.md)
- [下一步计划](docs/NEXT.md)
- [技术债务](docs/TECH_DEBT.md)

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
| `result.json` | 结构化推荐结果，适合程序读取 |
| `profile.json` | 本次使用的企业画像 |
| `config_manifest.json` | 本次运行使用的配置文件和 hash，方便复现 |
| `scenario_resolved.json` | 场景配置解析结果 |

批量运行时，会额外生成批次汇总：

| 文件 | 用途 |
|------|------|
| `batch_summary.csv` | 每家公司推荐结果汇总 |
| `batch_summary.json` | 结构化批量汇总 |
| `batch_quality_report.md` | 批量质量报告 |
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

当前系统会从 `data/` 读取 Prophet/NewEnt 风格的企业 JSON 文件，并构建本地 DuckDB 数据库。`.cache` 目录不需要放进来。

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

例如：

```bash
uv run xft recommend --no-llm "广东德美精细化工集团股份有限公司"
```

### 1.5 运行带 LLM 的推荐

如果已经配置好 LLM 密钥，可以运行：

```bash
uv run xft recommend "企业名称"
```

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

常用配置文件如下：

| 想调整什么 | 修改哪个文件 |
|------------|--------------|
| 推荐哪些产品、产品权重、命中规则 | `products.yaml` |
| 分析哪些维度、读取哪些字段、Web 搜索关键词 | `analysis_dimensions.yaml` |
| 评分加减分策略 | `scoring_policy.yaml` |
| 证据可信度、冲突处理、是否跳过 Web | `evidence_policy.yaml` |
| Web 搜索 provider、页数、缓存策略 | `web_search.yaml` |
| Web 证据抽取使用哪个模型 | `web_extract_llm.yaml` |
| LLM 提示词 | `prompts/*.md` |
| 场景入口和文件路径 | `scenario.yaml` |

### 3.1 场景入口：`scenario.yaml`

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

prompts:
  match_system: prompts/match_system.md
  recommend_system: prompts/recommend_system.md
  web_extract_system: prompts/extract_evidence_system.md
```

运行时通过 `--scenario` 指向这个目录即可：

```bash
uv run xft recommend --scenario config/scenarios/sales_recommendation "企业名称"
```

### 3.2 配产品：`products.yaml`

一个产品模块通常包含：

- `module_id`：产品唯一 ID，不建议随意改。
- `module_name`：报告里展示的产品名称。
- `priority`：同分时的排序优先级。
- `base_score`：基础分。
- `target_needs`：这个产品关注哪些分析维度。
- `match_rule`：业务解释。
- `positive_rules`：命中后加分的规则。
- `negative_rules`：信息缺失或不利信号的扣分规则。
- `exclusion_rules`：明显不适合时的排除规则。

示例：

```yaml
products:
  - module_id: crm_channel
    module_name: 客户与渠道管理(CRM)
    priority: 82
    base_score: 46
    target_needs:
      - sales_channel
      - business_product
    match_rule: 存在销售团队、跨区域市场、经销渠道或多客户触达线索的企业，优先考虑客户管理和渠道协同。
    positive_rules:
      - id: sales_channel_supported
        dimension_id: sales_channel
        evidence_type: supported
        weight: 16
        reason: 销售与渠道维度已有证据支持。
```

### 3.3 配分析维度：`analysis_dimensions.yaml`

维度决定系统从哪些角度分析企业。

一个维度通常包含：

- `id`：维度 ID。
- `level1/level2/level3`：报告展示用的分层名称。
- `local_fields`：优先从本地企业画像读取哪些字段。
- `evidence_templates`：把字段转成证据时使用的展示名称。
- `insufficient_evidence`：哪些信息缺失时需要提示。
- `support_rules`：本地字段满足条件时，自动生成分析判断。
- `web_search_queries`：本地信息不足时，用哪些关键词搜索 Web。

示例：

```yaml
dimensions:
  - id: business_product
    level1: 业务模式与产品特征
    level2: 行业与产业链定位
    level3: 主营业务与产品属性
    local_fields:
      - industry
      - business_scope
      - labels
    web_search_queries:
      - "{company_name} 官网 产品"
      - "{company_name} 主营产品"
      - "{company_name} 客户案例"
```

### 3.4 配评分策略：`scoring_policy.yaml`

这里控制评分的通用参数，例如：

- 分数上限和下限。
- 证据质量对分数的影响。
- 缺失证据的扣分方式。
- 推荐等级的阈值。

如果只是想调整产品匹配逻辑，通常优先改 `products.yaml`；只有要调整全局评分口径时，才改 `scoring_policy.yaml`。

### 3.5 配证据策略：`evidence_policy.yaml`

这里控制证据如何被使用，例如：

- 本地 JSON 信息足够时，是否跳过 Web 搜索。
- 不同来源的优先级。
- Web 信息和本地 JSON 冲突时如何处理。
- 证据质量分怎么计算。

默认原则是：本地 JSON 已经提供充分信息时，不重复搜索 Web；如果 Web 信息和本地 JSON 冲突，以本地 JSON 为准，并在报告中提示冲突。

### 3.6 配 Web 搜索：`web_search.yaml`

这里控制 Web 搜索和抓取，例如：

- 启用哪些 provider。
- 每个关键词取多少条结果。
- 是否抓取网页正文。
- 缓存目录。
- 是否复用已有搜索/抓取/抽取结果。

常用命令：

```bash
uv run xft web enrich --refresh-search "企业名称"       # 仅重新搜索
uv run xft web enrich --refresh-fetch "企业名称"        # 仅重新抓取网页
uv run xft web enrich --refresh-extraction "企业名称"   # 仅重新抽取证据
```

### 3.7 配 LLM：`web_extract_llm.yaml` 和 prompts

`web_extract_llm.yaml` 控制 Web 证据抽取使用的模型和参数。

`prompts/` 目录控制 LLM 的提示词。常见文件：

| 文件 | 用途 |
|------|------|
| `match_system.md` | 产品匹配时的系统提示词 |
| `recommend_system.md` | 生成推荐理由时的系统提示词 |
| `extract_evidence_system.md` | 从 Web 内容抽取证据时的系统提示词 |

修改 prompt 后，如果想让 Web 证据重新抽取，可以运行：

```bash
uv run xft web enrich --refresh-extraction "企业名称"
```

## 4. 新增一个业务场景

推荐做法是复制已有场景，或者通过 `extends` 继承已有场景，只改差异。

例如 `config/scenarios/bank_marketing/scenario.yaml` 可以继承销售推荐场景，然后只调整部分产品规则：

```yaml
extends: ../sales_recommendation
id: bank_marketing
name: 银行业营销场景

patches:
  products:
    - module_id: crm_channel
      set:
        base_score: 55
      append_positive_rules:
        - id: bank_high_quality_customer
          source_field: bank_flags.high_quality_customer
          op: "=="
          value: true
          weight: 12
          reason: 银行高质量客户标签提示金融服务匹配度更高
```

这样不需要复制整份 `products.yaml`，只维护这个场景和默认场景不同的部分。

新增或修改场景后，建议先校验：

```bash
uv run xft scenario validate config/scenarios/bank_marketing
```

## 5. 配置 API Key

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

## 6. Docker 使用方法

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

## 7. 常见问题

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
