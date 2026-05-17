# 统一企业分析平台

本项目已从早期"搜索 → 总结 → 合并报告"的单一脚本，演进为 **xft 统一企业分析平台**。平台以 DuckDB 事实层为中心，将通用能力（数据仓库、证据管理、Web 采集、规则评分、AI 调用、批量运行）下沉为共享基础设施，之上按业务场景搭建独立流水线。

```mermaid
flowchart TB
  subgraph Sources["Data Sources"]
    A["Prophet / NewEnt JSON"]
    B["Web Search & Crawl"]
    C["Manual Evidence"]
  end

  subgraph Ingestion["Ingestion"]
    D["etl_json_to_duckdb.py"]
    E["run_web_enrichment.py"]
    F["etl_web_to_duckdb.py"]
  end

  subgraph Warehouse["DuckDB Warehouse"]
    G["Bronze → Silver → Gold"]
    H["company_profile"]
    I["unified_evidence"]
  end

  subgraph Platform["xft Platform Core"]
    J["warehouse<br/>企业画像读取"]
    K["evidence<br/>证据仓库 & 冲突消解"]
    L["web<br/>搜索/抓取/抽取/缓存"]
    M["scoring<br/>规则评分引擎"]
    N["ai<br/>LLM 客户端"]
    O["core<br/>通用模型 & 场景配置"]
    P["runtime<br/>批量运行/质量/校准/交付"]
  end

  subgraph Pipelines["Scenario Pipelines"]
    Q["pipeline/recommender<br/>销售产品推荐"]
    R["pipeline/diligence<br/>企业尽调报告"]
  end

  subgraph Outputs["Outputs"]
    S["result.json"]
    T["report.md"]
    U["batch_quality_report"]
    V["delivery_manifest"]
  end

  A --> D --> G
  B --> E --> F --> I
  C --> I
  G --> H --> I
  H --> Pipelines
  I --> K --> Pipelines
  J --> Pipelines
  L --> Pipelines
  M --> Pipelines
  N --> Pipelines
  O --> Pipelines
  P --> Pipelines
  Pipelines --> Outputs
```

**平台分层原则：**

| 层 | 职责 | 不关心 |
|----|------|--------|
| `warehouse` | 企业事实入库与画像读取 | 推荐、尽调、营销 |
| `evidence` | 证据建模、去重、冲突、置信度 | 产品模块 |
| `web` | 搜索、抓取、LLM 抽取、缓存 | 推荐维度模型 |
| `scoring` | 规则评分、可追溯打分 | 具体产品定义 |
| `ai` | LLM 客户端、JSON 提取 | 业务 prompt |
| `core` | 通用模型、场景配置、维度分析 | 场景专有逻辑 |
| `runtime` | 批量执行、质量报告、校准、交付 | 场景专有指标 |
| `pipeline/*` | 场景编排、场景模型、prompt、报告 | — |

当前已落地两条流水线：

- **`pipeline/recommender`** — 销售产品推荐场景（主力）。
- **`pipeline/diligence`** — 旧尽调报告流水线（已场景化，能力复用）。

## 当前主链路

平台提供两条主链路，共享同一套基础设施：

### 链路 1：产品推荐（主力场景）

```text
data/ Prophet/NewEnt JSON
  → etl_json_to_duckdb.py          # 本地 JSON 分层入库
  → DuckDB warehouse               # Bronze → Silver → Gold → unified_evidence
  → run_recommender.py             # 推荐流水线
  → recommendation_runs/.../report.md
```

推荐流水线（6 节点）：

```text
data_gather → dimension_analyze → web_evidence → llm_match → llm_recommend → save
```

可选 Web 补证链路：

```text
run_web_enrichment.py              # 搜索 → 抓取 → LLM 抽取 → 缓存
  → etl_web_to_duckdb.py           # Web 证据入库
  → run_recommender.py --with-web-evidence
```

自动补证（缓存缺失时触发搜索）：

```bash
uv run python run_recommender.py --with-web "企业名称"
uv run python run_recommender.py --with-web --refresh-web "企业名称"
```

### 链路 2：企业尽调报告

```text
data/ Prophet/NewEnt JSON
  → DuckDB warehouse
  → run_pipeline.py diligence "企业名称"
  → runtime_runs/.../report.md
```

### 统一入口

```bash
# 推荐场景
uv run python run_pipeline.py recommender --scenario config/scenarios/sales_recommendation "企业名称"

# 尽调场景
uv run python run_pipeline.py diligence --config config "企业名称"

# 批量校准
uv run python run_calibration.py --limit 30 --batch-id cal-01
```

## 平台架构

`xft` 采用分层设计，每层有明确的向上接口和边界约束：

```text
┌──────────────────────────────────────────────────┐
│                 Pipeline Layer                    │
│  pipeline/recommender  │  pipeline/diligence      │
│  场景模型 · prompt · 报告 · 编排                    │
└────────────────────────┬─────────────────────────┘
                         │ 依赖
┌────────────────────────┴─────────────────────────┐
│                 Platform Core                      │
│  warehouse  │ evidence │ web │ scoring │ ai       │
│  企业画像    证据管理    Web   规则评分   LLM       │
│                         │                          │
│  core       │ runtime                             │
│  通用模型    批量 · 质量 · 校准 · 交付               │
└────────────────────────┬─────────────────────────┘
                         │ 依赖
┌────────────────────────┴─────────────────────────┐
│                 Data Layer                         │
│  DuckDB Warehouse (Bronze → Silver → Gold)         │
│  data/web Cache                                   │
└──────────────────────────────────────────────────┘
```

**依赖规则：**
- Pipeline → Core：场景可以调用任何平台能力。
- Core ↔ Core：`warehouse`、`evidence`、`web`、`scoring`、`ai` 互不依赖，各自独立。
- `core` 提供跨层共享的通用模型（`AnalysisDimension`、`ScoreBreakdown`、`ScenarioConfig`）。
- `runtime` 提供统一的批量执行、质量报告、校准和交付协议，不绑定具体场景。
- Core → Data：平台层只依赖 DuckDB 和数据文件，不依赖具体 JSON 格式。

**当前模块清单：**

| 模块 | 包路径 | 说明 |
|------|--------|------|
| 数据仓库 | `xft.warehouse` | DuckDB 管理、Prophet JSON 入库、企业画像读取 |
| 证据管理 | `xft.evidence` | 统一证据模型、证据仓库、去重、冲突消解 |
| Web 采集 | `xft.web` | 搜索、抓取、LLM 抽取、多级缓存 |
| 规则评分 | `xft.scoring` | 配置驱动 positive/negative/exclusion 评分 |
| AI 工具 | `xft.ai` | LLM 客户端、JSON 提取、置信度工具 |
| 通用核心 | `xft.core` | 通用模型、场景配置、维度分析、配置读取 |
| 运行时 | `xft.runtime` | 统一 pipeline 协议、批量执行、质量报告、校准、交付 |
| 推荐场景 | `xft.pipeline.recommender` | 销售产品推荐 |
| 尽调场景 | `xft.pipeline.diligence` | 企业尽调报告 |

## 核心能力

### 1. 数据分层 (Bronze / Silver / Gold)

`etl_json_to_duckdb.py` 对 `data/` 做分层入库：

- Bronze：`raw_company_json` 保留每个原始 JSON 文件、文件 hash、抓取时间、解析状态。
- Silver：把常用实体解析为结构化表，例如企业主体、股东、人员、风险、招聘、招投标、资质。
- Gold：`company_profile` 把推荐常用字段压成一张宽表，作为推荐主入口。

原始数据永远可追溯；业务推荐不需要知道每个 Prophet/NewEnt JSON 的复杂路径；后续新增字段时可以先落 Bronze，再逐步提取到 Silver 或 Evidence。

### 2. 统一证据层

`unified_evidence` 把不同来源的信息统一成标准形状：

```text
evidence_id
company_name / credit_code
dimension_id
source_type: local_json | web | manual | rule
source_name / source_path / source_url / source_field
claim / value
confidence
authority_level
relation_to_profile: primary | supplement | confirmation | conflict | inference
conflict_note / resolution
raw_ref / created_at
```

本地 JSON 画像写成 `source_type=local_json`、`relation_to_profile=primary`；Web 抽取写成 `source_type=web`，并区分补充、佐证和冲突。推荐侧只需要理解 evidence，不需要关心底层来自 JSON、搜索摘要还是网页正文。

### 3. 证据仓库与冲突解决 (EvidenceRepository & Resolver)

`EvidenceRepository` 提供按企业、维度、来源类型、冲突关系查询的统一入口。`EvidenceResolver` 在每次推荐运行时对证据做实时处理：

- **去重**：按 `(claim, source_type, source_field, source_url)` 去重。
- **归并**：按 `source_field` 分组，同一字段上的多条证据合并比较。
- **冲突解决**：Web 与本地 JSON 冲突时，默认 `resolution=use_local`（本地优先）。
- **来源权威度 boost**：高权威来源（政府网站、官方备案）自动提升置信度一级。
- **质量评分 (0-100)**：综合 primary、confirmation、supplement、inference、conflict 数量计算维度证据质量分。
- **兜底提升**：当某维度无 primary 证据时，最好的 Web 证据自动提升为 primary。

推荐报告和 `result.json` 会输出每条证据的归属（本地/Web）、冲突标注和解决策略。

### 4. 配置驱动的产品评分引擎 (ScoreEngine)

产品评分从硬编码规则升级为配置驱动。每个产品模块可在 `products.yaml` 中定义：

```yaml
positive_rules:       # 命中加分
  - id: procurement_signal
    dimension_id: supply_chain_procurement
    evidence_type: supported
    weight: 20
    reason: 供应链维度已有证据支持
negative_rules:       # 命中扣分
  - id: missing_amount
    missing_evidence: 年采购金额
    penalty: 8
    reason: 缺少年采购金额
exclusion_rules:      # 命中降权（分数封顶 20）
  - id: inactive_company
    source_field: reg_status
    op: "!="
    value: 存续
    reason: 企业状态非存续
```

每条推荐输出完整的 `ScoreBreakdown`：

```text
base_priority         基础优先级分
dimension_support     维度覆盖分
evidence_support      本地证据分
web_support           Web 补证分
positive_score        命中正向规则加分
negative_score        命中负向规则扣分
missing_evidence_penalty  缺失证据扣分
conflict_penalty      冲突扣分
final_score           最终得分 (0-100)
matched_rules         命中的正向规则及证据 ID
penalty_rules         命中的扣分规则
exclusion_rules       命中的排除规则
```

同时输出 `evidence_trace`，每条推荐可追溯到具体证据 ID、来源、声明和置信度。LLM 路径的输出也会被评分引擎重新校准（`_with_explainability`），确保排序稳定、分数可解释。

### 5. 维度分析

`dimension_analyze` 根据 `analysis_dimensions.yaml` 工作。每个维度定义：

- 读取哪些本地字段。
- 如何把字段格式化为事实证据。
- 哪些证据不足需要 Web 或人工补充。
- 本地规则如何产生弱推断（`support_rules`）。
- Web 查询模板是什么。

维度分析输出结构化 `DimensionAnalysis`，包含：

```text
facts                 人类可读事实
inferences            规则推断
local_evidence        本地 JSON 证据 (EvidenceRecord)
inference_evidence    规则推断证据
web_evidence          Web 补证/佐证
conflicts             Web 与本地画像冲突
missing_evidence      仍缺失的证据项
status                supported | partial | insufficient
confidence            高 | 中 | 低 | 待补充
```

### 6. 产品匹配与推荐

推荐分两步：

1. `llm_match`：判断每个产品模块是否匹配企业当前需求，输出分数、置信度、理由和缺失证据。fallback 使用结构化 evidence 和冲突数量调整分数。
2. `llm_recommend`：基于匹配结果和评分引擎生成最终推荐列表、切入话术和报告摘要。LLM 输出会被评分引擎校准排序和分数。

如果 LLM 不可用，系统走 deterministic fallback，仍能生成可运行的 MVP 报告。LLM prompt 明确要求基于证据，不允许把 `web_search_queries` 或缺失证据当成事实。

### 7. Web 缓存与重放 (Multi-Level Cache)

Web enrichment 实现了多级缓存，支持选择性失效和重放：

```text
cache_index.json (v1.1)
  runs/{run_id}/
    queries[]          cache_key, provider_params_hash, result_count
    pages[]            url, content_hash, page_path
    extractions[]      extraction_cache_key, prompt_hash, evidence_count
```

缓存策略：

- **搜索缓存**：key 包含 provider 参数哈希、max_results、cache policy version。provider 配置变化自动失效。
- **抓取缓存**：按 content hash 复用 `pages/*.md`。同一 URL 跨 run 命中时复制页面文件。
- **抽取缓存**：key 包含结果指纹、prompt 版本、prompt 文件 hash、抽取模型、抽取配置 hash。prompt 或模型变化自动失效。

CLI 支持细粒度刷新：

```bash
run_web_enrichment.py --refresh-search       # 仅重搜
run_web_enrichment.py --refresh-fetch        # 仅重抓
run_web_enrichment.py --refresh-extraction   # 仅重抽
run_web_enrichment.py --extract-only --source-run-id web_xxx  # 从已有搜索结果重放抽取
```

每个 Web run 生成 `web_cache_report.json/md`，记录搜索/抓取/抽取的复用与重跑计数。

### 8. 场景 Bundle (Scenario)

支持不同业务场景拥有独立的产品、维度、prompt、Web provider 策略：

```text
config/scenarios/sales_recommendation/
  scenario.yaml              # 场景入口（id, name, 路径配置）
  products.yaml              # 场景专属产品
  analysis_dimensions.yaml   # 场景专属维度
  web_search.yaml            # 场景专属 Web 搜索
  web_extract_llm.yaml       # 场景专属抽取配置
  prompts/                   # 场景专属 prompt
```

CLI 使用：

```bash
run_recommender.py --scenario config/scenarios/sales_recommendation "企业名称"
run_web_enrichment.py --scenario config/scenarios/sales_recommendation "企业名称"
```

场景会自动切换产品、维度、prompt、Web 配置、推荐输出目录和 Web cache root。旧配置路径继续兼容。

### 9. 批量运行与质量报告 (Batch Runner)

支持从企业列表批量运行推荐并生成交付产物：

```bash
uv run python run_recommender.py \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --with-web-evidence \
  --batch-id batch_sales_demo \
  --batch-output recommendation_runs/batches \
  --skip-existing
```

批量模式产出：

```text
recommendation_runs/batches/{batch_id}/
  batch_manifest.json         # 批次元信息
  companies.txt               # 企业清单
  runs/{company}/             # 每家企业独立目录
    report.md
    result.json
  batch_summary.json          # 汇总表（含证据计数、评分规则、冲突数）
  batch_summary.csv
  batch_quality_report.json   # 质量指标
  batch_quality_report.md     # 人类可读质量报告
  failed_companies.txt        # 失败清单（可做 --rerun-failed）
  delivery_manifest.json      # 交付文件清单
```

质量报告指标：

- 成功/部分成功/失败/跳过数量。
- 平均画像完整度、平均 Top 推荐分。
- Top 产品分布。
- 高冲突企业（冲突 ≥3 处）。
- 低完整度企业（完整度 < 60%）。
- 失败企业清单及错误原因。

`--skip-existing` 按稳定的企业目录名判断，可安全中断和续跑。

### 10. 人类可读进度输出

CLI 默认输出结构化中文进度，显示流水线各阶段的关键决策：

```text
═══ 开始分析：广东信华电器有限公司 ═══

📊 阶段 1/5: 加载企业画像
  ✓ DuckDB → company_profile 表 → 命中 (行业: 制造业, 完整度: 87%)
📊 阶段 2/5: 维度分析
  ✓ 10 个维度, 28 条事实 (supported:7 partial:3)
  ├─ hr_workforce: partial (需Web补充)
📊 阶段 3/5: Web 证据采集
  ✓ unified_evidence 表 → 22 条证据 → 3 个维度
  ├─ hr_workforce: 5条Web → 质量 75 (high)
📊 阶段 4/5: 产品匹配
  ✓ LLM 分析完成 → 8 个候选产品
📊 阶段 5/5: 生成报告
  ✓ report.md
```

Web 搜索时展示搜索查询、结果数、页面抓取状态、LLM 提取统计和相关性过滤。使用 `--verbose` 可同时输出 structlog 详细日志。

### 11. Web 证据相关性过滤

三层公司验证，确保 Web 证据确实关于目标企业：

1. **LLM prompt**：在 `extract_evidence_system.md` 中明确要求"先判断每个 Web 来源是否确实关于该目标企业"，不相关的返回空 claims。
2. **抓取前过滤** (`_should_fetch`)：提取公司核心名称（如"广东信华电器有限公司" → "信华电器"），页面标题或摘要不含核心名称的跳过抓取。
3. **抽取后过滤** (`_is_relevant_claim`)：claim 中必须出现公司全名或核心名称，否则丢弃。

同一 URL 只抓取一次（按 URL 去重）。

## 数据流向图

### 本地 JSON 到推荐报告

```mermaid
sequenceDiagram
  participant JSON as data/ JSON
  participant ETL as etl_json_to_duckdb.py
  participant DB as DuckDB
  participant Repo as EvidenceRepository
  participant Resolver as EvidenceResolver
  participant Engine as ScoreEngine
  participant Rec as run_recommender.py
  participant Out as recommendation_runs/

  JSON->>ETL: 读取企业目录和 *.json
  ETL->>DB: 写 raw_company_json
  ETL->>DB: 写 Silver fact tables
  ETL->>DB: 生成 company_profile
  ETL->>DB: 生成 unified_evidence(local_json)
  Rec->>DB: 读取 company_profile
  Rec->>Repo: 查询 unified_evidence
  Repo->>Resolver: 去重、归并、冲突解决、质量评分
  Resolver->>Rec: ResolvedDimensionEvidence
  Rec->>Engine: 评分上下文 (profile + analyses)
  Engine->>Rec: ScoringRunResult (per product)
  Rec->>Rec: 维度分析、产品匹配、推荐排序
  Rec->>Out: 写 profile.json / result.json / report.md
```

### Web 补证到 DuckDB

```mermaid
sequenceDiagram
  participant CLI as run_web_enrichment.py
  participant DB as DuckDB
  participant Plan as Web Planner
  participant Provider as MiniMax/Metaso
  participant Fetch as crawl4ai
  participant LLM as Evidence Extractor
  participant Cache as data/web

  CLI->>DB: 读取 company_profile
  CLI->>Plan: 根据维度证据判断是否需要搜索
  Plan-->>CLI: planned / skipped queries
  CLI->>Provider: 执行搜索查询
  Provider-->>Cache: 保存 provider_responses/*.json
  CLI->>Fetch: 抓取搜索结果页面（含公司名匹配过滤）
  Fetch-->>Cache: 保存 pages/*.md 和 fetched_pages.jsonl
  CLI->>LLM: 抽取证据（含公司相关性校验）
  LLM-->>Cache: 保存 extraction_requests/results 和 web_evidence.jsonl
  CLI->>DB: 可选自动导入 web_* tables 和 unified_evidence(web)
```

Web 补证遵循"本地 JSON 优先"：

- 如果本地画像已经覆盖某个维度，planner 默认跳过该维度的 Web 搜索。
- 如果 Web 信息与 JSON 信息冲突，`relation_to_profile=conflict`，并默认 `resolution=use_local`。
- 原始响应、页面正文、中间抽取请求和抽取结果都会保留在 `data/web/`，便于审计和重放。

## DuckDB 分层

```text
Bronze
  raw_company_json
  company_import_status

Silver
  companies / company_labels / key_personnel / shareholders
  ip_summary / risk_features / recruitments / bidding_summary
  qualifications / branches / financing_events / outbound_investments

Gold / Evidence
  company_profile
  web_search_runs / web_search_queries / web_search_results
  web_pages / web_evidence
  unified_evidence
```

## 关键入口

```text
etl_json_to_duckdb.py              # data/ JSON -> DuckDB
run_web_enrichment.py              # Web 搜索、抓取、抽取、缓存
etl_web_to_duckdb.py               # data/web -> DuckDB Web 表
run_recommender.py                 # 推荐主入口
run_pipeline.py                    # 统一流水线入口 (recommender / diligence)
run_calibration.py                 # 推荐规则批量校准

src/xft/                           # 平台根包（规范包名）
src/xft/core/                      # 通用模型、scenario bundle、维度分析、配置读取
src/xft/warehouse/                 # DuckDB 本地仓库与企业画像
src/xft/evidence/                  # 统一证据模型、仓库、冲突解决
src/xft/ai/                        # 公共 LLM client / JSON 抽取工具
src/xft/web/                       # Web enrichment 服务与缓存
src/xft/scoring/                   # 配置驱动评分引擎
src/xft/runtime/                   # 统一 pipeline request/result、质量报告、交付清单、校准
src/xft/pipeline/recommender/      # 销售产品推荐场景
src/xft/pipeline/diligence/        # 旧尽调流水线（已场景化）
src/xft/nodes/                     # 兼容转发层 → xft.pipeline.diligence.nodes
```

所有入口脚本已使用 `xft.*` import；`src/diligence` 旧包名目录已经删除，新代码请统一使用 `xft.*`。

## 常用命令

初始化或重建本地 JSON 仓库：

```bash
uv run python etl_json_to_duckdb.py --input data --output cache/company_warehouse.duckdb
```

离线跑推荐：

```bash
uv run python run_recommender.py --no-llm "企业名称"
```

单独准备 Web 缓存但不入库：

```bash
uv run python run_web_enrichment.py --no-etl "企业名称"
```

选择性重跑 Web enrichment：

```bash
uv run python run_web_enrichment.py --refresh-search "企业名称"
uv run python run_web_enrichment.py --refresh-extraction "企业名称"
uv run python run_web_enrichment.py --extract-only --source-run-id web_xxx "企业名称"
```

从 Web 缓存重建 DuckDB Web 表：

```bash
uv run python etl_web_to_duckdb.py --input data/web --warehouse cache/company_warehouse.duckdb --rebuild
```

读取已有 Web 证据生成推荐：

```bash
uv run python run_recommender.py --with-web-evidence "企业名称"
```

自动补证并推荐（缓存缺失时触发搜索）：

```bash
uv run python run_recommender.py --with-web "企业名称"
```

按场景运行推荐：

```bash
uv run python run_recommender.py \
  --scenario config/scenarios/sales_recommendation \
  "企业名称"
```

批量运行并生成交付产物：

```bash
uv run python run_recommender.py \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --with-web-evidence \
  --batch-id batch_sales_demo \
  --batch-output recommendation_runs/batches \
  --skip-existing
```

统一流水线入口（支持 recommender / diligence）：

```bash
uv run python run_pipeline.py recommender --scenario config/scenarios/sales_recommendation "企业名称"
uv run python run_pipeline.py diligence --config config "企业名称"
```

批量校准推荐规则：

```bash
uv run python run_calibration.py --limit 10 --batch-id calibration-run-01
uv run python run_calibration.py --labels calibration_labels.csv --limit 30
```

真实 Web / LLM 小批次校准：

```bash
uv run python run_calibration.py \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --limit 5 \
  --with-web \
  --with-llm \
  --batch-id web-calibration-01
```

默认情况下，本地画像已经充足的维度会跳过 Web 搜索。若要专门压测搜索、抓取、LLM 抽取和入库链路，可加：

```bash
uv run python run_calibration.py \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --limit 1 \
  --with-web \
  --with-llm \
  --force-web-dimensions \
  --batch-id web-calibration-force-01
```

业务标注校准：

```bash
cp calibration_labels.example.csv calibration_labels.csv

uv run python run_calibration.py \
  --scenario config/scenarios/sales_recommendation \
  --labels calibration_labels.csv \
  --limit 30 \
  --batch-id calibration-label-01
```

`calibration_labels.csv` 字段：

```text
company_name,expected_top_module,acceptable_modules,comment
```

- `company_name`：企业名称，需与 batch summary 中公司名一致。
- `expected_top_module`：业务认为最理想的 Top1 产品模块 ID。
- `acceptable_modules`：可接受模块列表，支持英文逗号、中文逗号、分号和竖线分隔。
- `comment`：业务备注，会进入错配案例，便于复盘。

校准输出位于 `recommendation_runs/calibration/{batch_id}/calibration_report.md` 和 `calibration_report.json`。
Web/LLM 校准还会生成 `web_llm_review_samples.csv`，用于人工复核搜索证据是否属于目标公司、是否被正确过滤、是否与本地 JSON 冲突。
重点看 `Top1 命中率`、`可接受命中率`、Web 证据覆盖率和错配案例，再调整 `products.yaml`、`scoring_policy.yaml`、`evidence_policy.yaml` 或 prompts。

## 配置

兼容三种配置方式：

**1. 传统平铺配置：**

```text
config/recommender/products.yaml
config/recommender/analysis_dimensions.yaml
config/recommender/web_search.yaml
config/recommender/web_extract_llm.yaml
config/scoring_policy.yaml
config/evidence_policy.yaml
config/recommender/prompts/
```

**2. 场景 Bundle：**

```text
config/scenarios/sales_recommendation/
  scenario.yaml
  products.yaml
  analysis_dimensions.yaml
  web_search.yaml
  web_extract_llm.yaml
  scoring_policy.yaml
  evidence_policy.yaml
  prompts/
```

`--scenario` 是推荐给业务人员使用的主入口。它会同时切换产品、维度、prompt、Web 配置、评分策略、证据策略、推荐输出目录和 Web cache root。新增业务场景时，优先复制 `config/scenarios/sales_recommendation/` 并在该目录内修改配置。

`scenario.yaml` 负责声明这一套配置文件的位置：

```yaml
id: sales_recommendation
name: 销售产品推荐

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

如果只想审计解析后的路径，可以运行：

```bash
uv run python run_pipeline.py recommender \
  --scenario config/scenarios/sales_recommendation \
  --write-scenario-resolved \
  "企业名称"
```

**3. 产品评分规则（在 products.yaml 中）：**

```yaml
- module_id: procurement_srm
  module_name: 供应商关系管理(SRM)
  priority: 90
  base_score: 45
  target_needs: [supply_chain_procurement, business_product]
  match_rule: 制造业、采购链条较长的企业...
  positive_rules:
    - id: procurement_signal
      dimension_id: supply_chain_procurement
      evidence_type: supported
      weight: 20
      reason: 供应链维度已有证据支持
  negative_rules:
    - id: missing_amount
      missing_evidence: 年采购金额
      penalty: 8
      reason: 缺少年采购金额
  exclusion_rules:
    - id: inactive_company
      source_field: reg_status
      op: "!="
      value: 存续
      reason: 企业状态非存续
```

### 维度配置 (`analysis_dimensions.yaml`)

每个维度是一个独立条目，完整字段如下：

```yaml
dimensions:
  - id: supply_chain_procurement     # 唯一标识 (snake_case)
    level1: 供应链与采购管理          # 一级分类
    level2: 采购规模与特征            # 二级分类
    level3: 供应链复杂度              # 三级分类
    role: 供应链管理与商业调研专家     # LLM 角色描述
    local_fields:                    # 从 company_profile 读取的字段
      - industry
      - employee_count
      - business_scope
      - bidding_total
      - qualification_count
    evidence_templates:              # 报告中展示的证据项 (字段→中文标签)
      - field: industry
        label: 行业
      - field: employee_count
        label: 员工规模
      - field: bidding_total
        label: 招投标数量
    insufficient_evidence:           # 预期但缺失的证据，报告标记为"数据缺口"
      - 供应商数量
      - 前五大供应商集中度
      - 年采购金额
    analysis_prompt: |               # LLM 维度分析系统提示词
      判断企业是否存在采购协同、供应商准入、供应商绩效等数字化需求。
      只能基于已提供证据分析，不得编造供应商数量、采购金额。
    evidence_policy: |               # 证据强度说明
      直接采购数据优先于行业和规模推断。制造业、员工规模只能作为间接线索。
    support_rules:                   # 可选：本地自动推断规则
      - field: employee_count
        op: ">="
        value: 200
        claim: 员工规模较大，可能存在采购流程协同与供应商管理需求。
        confidence: 低
      - field: bidding_total
        op: ">"
        value: 0
        claim: 存在招投标记录，可作为项目型采购管理复杂度线索。
        confidence: 低
    web_search_queries:              # 可选：Web 搜索查询模板
      - "{company_name} 供应商"       # {company_name} 运行时自动替换
      - "{company_name} 采购"
      - "{company_name} 招投标"
```

**字段说明：**

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `id` | ✓ | 唯一标识，被 `products.yaml` 的 `target_needs` 和 `dimension_id` 引用 |
| `level1/2/3` | ✓ | 三级分类，用于报告分组展示 |
| `local_fields` | ✓ | 必须与 `company_profile` 表列名一致，支持嵌套路径如 `ip_counts.patent` |
| `evidence_templates` | | 字段到中文标签的映射，报告展示用 |
| `insufficient_evidence` | | 缺失证据列表，报告会输出为"建议进一步核实" |
| `support_rules` | | 本地规则推断，fallback 模式下自动执行，LLM 模式作为参考上下文 |
| `web_search_queries` | | 使用 `{company_name}` 占位符，同时支持 `{industry}` 和 `{industry_big}` |

**`op` 操作符：** `==` `!=` `>` `>=` `<` `<=` `contains` `exists`

**`confidence` 取值：** `高` `中` `低`

新增维度只需在 YAML 中添加一个条目，无需改代码。维度会自动出现在报告和 `result.json` 中。

### 产品规则配置 (`products.yaml`)

每个产品模块支持三种规则：

```yaml
products:
  - module_id: procurement_srm             # 唯一标识
    module_name: 供应商关系管理(SRM)        # 展示名称
    priority: 90                           # 基础优先级 (0-100)
    base_score: 50                         # 基础分
    target_needs:                          # 关联维度 (引用 dimension.id)
      - supply_chain_procurement
      - business_product
    match_rule: 制造业、采购链条较长的企业...  # LLM 匹配规则描述

    # ── 正向规则：命中加分 ──
    positive_rules:
      # 维度状态匹配
      - id: procurement_dimension_supported
        dimension_id: supply_chain_procurement
        evidence_type: supported            # supported | partial | insufficient
        weight: 18                          # 加分值
        reason: 供应链维度已有证据支持
      # 画像字段条件
      - id: bidding_signal
        source_field: bidding_total          # company_profile 字段名
        op: ">"                              # 操作符
        value: 0                             # 阈值
        weight: 10
        reason: 存在招投标记录

    # ── 负向规则：命中扣分 ──
    negative_rules:
      # 证据缺失
      - id: missing_supplier_count
        missing_evidence: 供应商数量
        penalty: 5                           # 扣分值
        reason: 缺少供应商数量
      # 存在冲突
      - id: conflict_penalty
        relation_to_profile: conflict
        penalty: 8
        reason: 存在 Web 与本地画像冲突

    # ── 排除规则：命中排除该产品 ──
    exclusion_rules:
      - id: inactive_company
        source_field: reg_status
        op: "!="
        value: 存续
        reason: 企业状态非存续
```

**规则类型一览：**

| 规则类型 | 触发条件 | 效果 |
|----------|----------|------|
| `positive_rules` | `dimension_id` + `evidence_type` 匹配 | 加 `weight` 分 |
| `positive_rules` | `source_field` + `op` + `value` 匹配 | 加 `weight` 分 |
| `negative_rules` | `missing_evidence` 命中 | 扣 `penalty` 分 |
| `negative_rules` | `relation_to_profile: conflict` 存在冲突 | 扣 `penalty` 分 |
| `exclusion_rules` | `source_field` + `op` + `value` 匹配 | 产品被排除 (`excluded: true`) |

**`source_field` 支持嵌套路径**，例如 `ip_counts.patent`、`bank_flags.high_quality_customer`、`risk_counts.self`、`cross_border_flags.labels`。

**评分公式：**

```text
final_score = base_score
            + dimension_support      # 关联维度覆盖分
            + evidence_support       # 本地证据分
            + web_support            # Web 补证分
            + positive_score         # 命中 positive_rules 加分总和
            - negative_score         # 命中 negative_rules 扣分总和
            - missing_evidence_penalty  # 缺失证据扣分
            - conflict_penalty       # 冲突扣分
```

修改 YAML 后重新跑推荐即可看到评分变化。`result.json` 中 `score_breakdown.matched_rules` / `penalty_rules` / `exclusion_rules` 会列出每条命中的规则及证据 ID，`report.md` 会展示分项得分和命中规则。修改规则不影响 LLM 匹配逻辑，但会影响最终排序和分数。

### 评分策略 (`scoring_policy.yaml`)

`scoring_policy.yaml` 控制推荐排序的通用分值，不需要改代码：

```yaml
dimension_support:
  supported_score: 5
  partial_score: 2

evidence_support:
  per_item: 1
  cap: 8

web_support:
  confirmation_per_item: 2
  confirmation_cap: 8
  supplement_per_item: 1
  supplement_cap: 5

penalties:
  conflict_per_item: 8
  missing_evidence_cap: 15

exclusion:
  score_cap: 20
```

调这个文件会影响所有产品的基础排序逻辑；调 `products.yaml` 会影响某个产品自己的正向、负向和排除规则。

### 证据策略 (`evidence_policy.yaml`)

`evidence_policy.yaml` 控制 Web 是否补证、证据质量分、冲突消解和推荐状态阈值：

```yaml
web_planning:
  supported_facts_to_skip_web: 3

dimension_analysis:
  supported_facts_threshold: 3

resolver:
  source_priority:
    local_json: 0
    manual: 1
    rule: 2
    web: 3
    llm_extraction: 4
  quality_score:
    primary: 15
    confirmation: 10
    supplement: 5
    inference: 3
    conflict_penalty: 10

recommender:
  max_web_evidence_per_dimension: 5
  supported_quality_threshold: 45
  partial_quality_threshold: 15
```

典型改法：

- 本地画像已经比较完整、想减少 Web 搜索：调低 `supported_facts_to_skip_web`。
- 想让 Web 外部佐证更影响推荐状态：提高 `confirmation` 或降低 `supported_quality_threshold`。
- 想更保守地处理冲突：提高 `conflict_penalty`。

## 输出文件

每次推荐运行生成：

```text
recommendation_runs/{run_id}/
  profile.json               # 企业画像
  dimension_analysis.json    # 维度分析（含 evidence）
  match_results.json         # 产品匹配结果
  result.json                # 结构化推荐结果
  report.md                  # Markdown 推荐报告
```

`result.json` 结构：

```text
company_name / scenario / scenario_name
summary
recommendations[]
  rank / module_id / module_name / score / priority
  business_need / reason / suggested_pitch
  evidence_dimensions / data_gaps
  score_breakdown            # 完整分项得分
    base_priority / dimension_support / evidence_support
    web_support / positive_score / negative_score
    missing_evidence_penalty / conflict_penalty
    final_score / excluded
    matched_rules[] / penalty_rules[] / exclusion_rules[]
  evidence_trace[]           # 每条推荐的证据溯源
    evidence_id / dimension_id / source_type
    source_name / source_url / source_field
    claim / confidence / relation_to_profile
evidence_summary             # 全局证据统计
  local_evidence_count / web_evidence_count
  conflict_count / missing_evidence_count
  by_dimension[]
conflict_summary[]           # 冲突清单
scoring_summary              # 评分运行统计
needs_web_enrichment / profile_completeness
```

## 设计原则

**数据原则：**
- 本地事实层优先，Web search 后补。
- 原始数据完整保留（Bronze），结构化解析逐步推进（Silver → Gold）。
- 所有场景只依赖稳定 Gold 层和 unified_evidence，不直接绑定 JSON 文件形状。

**平台原则：**
- 分层隔离：`warehouse`、`evidence`、`web`、`scoring`、`ai` 互不依赖，各自独立。
- 场景独立：每个 pipeline 拥有独立的产品、维度、prompt、报告，互不干扰。
- 运行时无关：`runtime` 不绑定具体场景，通过统一协议（`PipelineRunRequest` / `PipelineRunResult`）驱动任意流水线。

**质量原则：**
- 证据不足显式表达（`insufficient_evidence`），不用模型想象补齐。
- 评分可追溯：每条推荐可追溯到具体证据 ID 和命中规则。
- 批量可交付：一次运行产出报告、质量指标、交付清单和校准数据。

**配置原则：**
- 配置优先：产品规则、维度、评分策略全部外置到 YAML。
- 场景可继承：`extends` / `overrides` 减少跨场景复制，`scenario_resolved.json` 可审计。

## 当前限制

- Web 抽取 LLM 底层 client 暂时复用项目现有 OpenAI-compatible client。
- `dimension_analyze` 的本地推断规则较轻量，适合作为 MVP，不等于完整专家判断。
- 复杂字段还没有全部从 47 类 JSON 中解析出来。
- `company_profile` 是当前唯一稳定 Gold 接口，未来可以增加更多 Gold 表。
- 当前报告是 Markdown 简报，不是最终商业交付版报告。
- Web provider 类型目前内置 `minimax` / `metaso`；业务人员可通过配置启停和调参，新增 provider adapter 仍需要开发。
- Scenario bundle 已覆盖产品、维度、Web、LLM、评分、证据策略，但还没有对 `products.yaml` 内单个产品规则做结构化 patch。
- Web/LLM 运行指标（搜索/抓取/抽取 执行与复用次数、LLM fallback 比例）尚未标准化接入质量报告。

## 后续计划

已完成（详见 `NEXT.md`）：

- **Sprint A**：finished。
- **Sprint B**：`xft.web` 与 `xft.scoring` 解耦对 `pipeline/recommender` 的反向依赖，下沉通用模型到 `xft.core`。
- **Sprint C**：旧尽调流水线迁入 `xft.pipeline/diligence`。
- **Sprint D**：统一 pipeline request/result 协议与 `run_pipeline.py` 入口。
- **Sprint E**：质量报告、交付清单、失败清单平台化到 `xft.runtime.artifacts`。
- **Sprint F**：Scenario bundle 继承与配置解析审计（`extends` / `overrides` / `scenario_resolved.json`）。
- **Sprint G**：推荐规则批量校准工具（`run_calibration.py`）、评分饱和修复。
- **Config Sprint**：`src/diligence` 旧目录删除；`scoring_policy.yaml` 和 `evidence_policy.yaml` 纳入 scenario bundle，业务策略可通过 YAML 调整。
- **Sprint H**：业务标注校准 CLI 闭环，支持 `--labels calibration_labels.csv` 计算 Top1/可接受命中率和错配案例。
- **Sprint K**：真实 Web / LLM 小批次校准闭环，支持 `--force-web-dimensions` 压测搜索/抓取/抽取链路，并输出 `web_llm_review_samples.csv`。

当前优先事项（详见 `TECH_DEBT.md`）：

- 配置 patch：支持按 `module_id` 局部覆盖产品规则，减少场景复制。
- 配置审计 manifest：报告交付时记录配置内容 hash，保证可复现。
- 扩大真实校准样本：补业务标注 CSV 后跑 5-10 家，继续验证推荐命中率。
- Provider 扩展：按 adapter 接口增加 Bing / Tavily / SerpAPI 等搜索源。
- 增加第二个真实业务场景（如 `bank_marketing`），验证场景继承设计的完备性。
- 增加 `.xlsx` 汇总交付和 `.zip` 交付包。
- 增加 DuckDB schema version 和 migration 策略。
- 增加 `--rerun-failed` 直接读取上一批 `failed_companies.txt`。
