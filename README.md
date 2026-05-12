# xft-dd · 企业尽调自动化工具

基于多源检索增强与 AI 推理的企业尽职调查工具。输入企业名称，自动生成覆盖 8 个维度的结构化尽调报告。

---

## 快速开始

```bash
# 复制环境变量模板，填写 API 凭证
cp .env.example .env

# 单企业尽调
uv run main.py "佛山市固特家居制品有限公司"

# 仅处理指定维度
uv run main.py "某公司" --only basic_info,tech_cert

# 排除指定维度
uv run main.py "某公司" --skip listing

# 预览搜索查询词，不发起网络请求
uv run main.py "某公司" --dry-run

# 批量处理（支持 .txt 或 .csv）
uv run main.py --batch companies.txt
uv run main.py --batch companies.csv --name-column company_name

# 批量续跑（跳过已完成企业）
uv run main.py --batch companies.txt --resume --batch-dir batch_runs/20260510-...
```

---

## 环境配置

复制 `.env.example`，按需填写：

```env
# ── 搜索层（不可替换）──────────────────────────────────────────
MINIMAX_API_KEY=SM4:<加密后的密钥>
MINIMAX_BASE_URL=https://api.minimax.io/v1

# ── 秘塔 AI 搜索（可选）────────────────────────────────────────
METASO_API_KEY=
METASO_ENABLED=false
METASO_VERIFY_TLS=true

# ── 推理层（可替换为任意 OpenAI 兼容模型）───────────────────────
# 若 LLM_API_KEY 为空则自动复用 MINIMAX_API_KEY
LLM_API_KEY=
LLM_BASE_URL=https://api.minimax.io/v1
LLM_MODEL=MiniMax-M2.7-Highspeed

# 使用 DeepSeek 示例：
# LLM_API_KEY=SM4:<加密后的密钥>
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_MODEL=deepseek-chat
```

---

## 整体架构

管道基于 LangGraph 构建，7 个节点组成有向图，每个维度作为独立分支并行执行：

```
输入: 企业名称
  │
  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  LangGraph Pipeline                                                   │
│                                                                       │
│  init_node                                                            │
│    → 生成 run_id，过滤已启用维度，创建输出目录                       │
│    │                                                                  │
│    ▼  (Send API 扇出，受 dimension_concurrency 限制)                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐    │
│  │ search_summarize │  │ search_summarize │  │ ...×N             │    │
│  │ (basic_info)     │  │ (tech_cert)      │  │                   │    │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬────────────┘    │
│           │  ┌──────────────────┘                    │                │
│           │  │  ┌───────────────────────────────────┘                │
│           ▼  ▼  ▼                                                     │
│  collect_node → 检查必要维度完整性                                   │
│    │                                                                  │
│    ▼                                                                  │
│  merge_node → LLM 合并各维度摘要，生成 Markdown 报告                 │
│    │                                                                  │
│    ▼                                                                  │
│  save_node → 写入产物文件，打印成本摘要                              │
└──────────────────────────────────────────────────────────────────────┘
  │
  ▼
输出: runs/{run_id}/
  ├── final_report.md
  ├── dimension_summaries.json
  ├── raw_search_results.json
  └── run_meta.json              ← 含本次 API 调用成本
```

### search_summarize_node 内部流程（每维度独立并行）

```
search_node                           summarize_node
──────────                            ──────────────

MiniMax Search API
  │  dim.minimax_queries 并发查询
  │  返回 SearchItem（snippet 有值，full_text=""）
  ▼
去重（按 URL 优先）
  │
  ▼
Metaso 补充（可选）
  │  chat 模式: AI 综合答案 → SearchItem（source="metaso_chat"，full_text=答案）
  │  search 模式: 网页 URL + rawContent → SearchItem（source="metaso_search"）
  │  → prepend 到 items 前面
  ▼
crawl4ai 全文抓取（可选）
  │  对每个 item 检查 _should_fetch():
  │    ✅ url 非空 & 非 metaso://
  │    ✅ target in title OR target in snippet
  │    ✅ url 不含 fetch_blocked_domains
  │  → 成功: item.full_text = 页面 markdown[:max_full_text_chars]
  │           item.snippet = full_text[:300]
  │  → 失败/跳过: item 保持原样
  ▼
DimensionSearchResult            ──────────────────────────────────────►
                                         │
                                    [NEW] 结构化字段提取（可选）
                                         │  if dim.extract_fields:
                                         │    收集所有 full_text 非空 item
                                         │    → 1 次 LLM 调用，逐源提取字段值
                                         │    → _ExtractionsResult
                                         │    → 写入 dsr.extractions
                                         │    → _format_extraction_table()
                                         ▼
                                    主 summarize
                                         │  _render_results(dsr, extraction_table)
                                         │    - 有提取表: table + full_text[:2000]
                                         │    - 无提取表: full_text 原样
                                         │  → LLM 调用 → DimensionSummary
                                         ▼
                                    DimensionSummary
                                    (summary, confidence,
                                     uncertain_facts,
                                     evidence_item_ids)
```

---

## 检索与增强层

### 第一层：MiniMax Search — 网页搜索召回

**端点**：`POST /v1/coding_plan/search`  
**实现**：`src/diligence/utils/minimax_search.py`

- 对每个维度的 `minimax_queries` 并发发起搜索，受 `query_concurrency_per_dimension` 限流
- 返回网页列表（title + link + snippet），每查询最多 `max_results_per_query` 条
- 覆盖企查查、天眼查、招聘平台、新闻、政府公告等多类来源
- 多条查询结果自动去重（URL 优先匹配，无 URL 时降级为 title+snippet 匹配）
- 仅返回搜索结果摘要，**不返回网页全文**

### 第二层：Metaso 秘塔 AI — 精准问答与搜索（可选）

**端点**：
- `POST https://metaso.cn/api/v1/chat/completions`（chat 模式）
- `POST https://metaso.cn/api/v1/search`（search 模式）

**实现**：`src/diligence/utils/metaso.py`  
**启用方式**：`.env` 中设置 `METASO_ENABLED=true` 并填入 `METASO_API_KEY`

两种模式由 `dim.metaso_mode` 控制：

| 模式 | 返回内容 | credits 消耗 | 适用场景 |
|------|---------|:---:|------|
| `chat` | AI 综合答案 + 来源引用 | 6/查询 | 需要跨源判断的维度（background、tech_cert） |
| `search` | 真实网页 URL + summary + rawContent | 6×size/查询 | 需要原始数据的维度（basic_info、ip、product） |

- chat 模式：答案包装为 `metaso://` 协议的 SearchItem（`source="metaso_chat"`），不参与后续 crawl4ai 抓取
- search 模式：返回真实 HTTP URL 的 SearchItem（`source="metaso_search"`），可被 crawl4ai 再次抓取
- 公司名含 ASCII 括号时自动转换为全角 `（）` 再发送，避免查询解析歧义

### 第三层：crawl4ai — 全文抓取

**实现**：`src/diligence/utils/fetch.py`  
**启用方式**：维度设置 `fetch_enabled: true`

- 使用 `AsyncWebCrawler` 抓取页面并提取 Markdown 正文
- 支持 JS 渲染页面，自动处理 bot 检测和内容提取
- 抓取条件（`_should_fetch`）：
  - URL 非空且非 `metaso://` 协议
  - title 或 snippet 包含目标企业名称
  - URL 不含 `fetch_blocked_domains` 中的域名片段
- 正文截断到 `max_full_text_chars`（默认 6900）
- 该页正文 < 100 字符视为拦截页面，自动丢弃

### 结构化字段提取（NEW）

**实现**：`src/diligence/nodes/summarize_node.py` 中的 `_do_structured_extraction()`  
**启用方式**：维度配置 `extract_fields` 列表

- 在主 summarize 前，对每个有 `full_text` 的 item 做批量字段提取
- 一次 LLM 调用处理所有 source，逐源提取指定字段值并标注可信度
- 提取结果呈现为 Markdown 表格，同时序列化到 `raw_search_results.json` 的 `extractions` 字段
- 多源交叉验证：同一字段在不同 source 的值并排列出，冲突不隐藏
- 提取失败自动重试 1 次，仍失败则降级回原文直接 summarize

### 推理层：LLM — 摘要与报告生成

**实现**：`src/diligence/nodes/summarize_node.py`、`src/diligence/nodes/merge_node.py`

- **summarize_node**：接收提取表 + 截断原文，输出结构化 JSON（summary、confidence、uncertain_facts、evidence_item_ids）
- **merge_node**：合并各维度摘要，生成最终 Markdown 报告及人工核验清单
- 通过 `.env` 中的 `LLM_*` 变量切换模型，无需修改代码

### 检索策略分工

| 任务 | 使用层级 | 理由 |
|------|---------|------|
| 发现「企业出现在哪些网页」 | MiniMax Search | 覆盖面广，各类网站均可命中 |
| 查询「统一社会信用代码」等精确事实 | Metaso chat | 直接检索工商数据库，答案精准 |
| 获取真实 URL + 原始网页内容 | Metaso search | 返回可被 crawl4ai 二次抓取的真实页面 |
| 获取详情页完整正文 | crawl4ai | 突破搜索摘要的字数限制 |
| 从正文中逐源提取关键字段 | 结构化提取 | 专注任务，多源交叉验证 |
| 裁决冲突信息、输出结构化摘要 | LLM | 需要跨来源推理和格式控制 |

---

## 8 个尽调维度

| 维度 ID | 名称 | 是否必需 |
|---------|------|:---:|
| `basic_info` | 工商基本信息 | ✅ |
| `industry` | 行业与细分 | |
| `scale` | 员工规模 | |
| `background` | 企业背景 | |
| `tech_cert` | 科技属性资质 | |
| `ip` | 知识产权 | |
| `product` | 产品与定位 | |
| `listing` | 上市情况 | |

`required=true` 的维度失败时，`run_meta.json` 中 `required_failed` 标记为 `true`，进程退出码为 2。

---

## 配置说明

核心配置位于 `config.yaml`，维度定义与管道参数均可通过配置文件调整，无需修改代码：

```yaml
schema_version: "1.0"

# ── 并发控制 ──────────────────────────────────────────────────
dimension_concurrency: 8             # 最大并行维度数（1-20）
query_concurrency_per_dimension: 5   # 每个维度内最大并行搜索数（1-10）
search_timeout_seconds: 30           # 单次搜索超时（秒）
max_results_per_query: 10            # 每条查询最大结果数

# ── 输出 ──────────────────────────────────────────────────────
runs_dir: "runs"                     # 单企业产物根目录

# ── AI 提示词（可按行业定制）──────────────────────────────────
summarize_system_prompt: "你是企业尽调专家..."
merge_system_prompt: "你是行业顶级专家..."

# ── crawl4ai 抓取参数（全局默认值）────────────────────────────
max_full_text_chars: 6900            # 抓取正文最大长度（100-100000）
crawl_fetch_timeout: 25              # 单页抓取超时（秒，5-120）
crawl_fetch_concurrency: 2           # 并行抓取数（1-5）

# ── 抓取域名黑名单 ────────────────────────────────────────────
# URL 中包含任意一项即跳过 crawl4ai 全文抓取
# 留空则 fetch_enabled: true 时抓取全部非 metaso 页面
fetch_blocked_domains:
  - "qixin.com"
  - "qcc.com"

# ── 结构化字段提取（全局默认值，可被维度覆盖）─────────────────
extract_system_prompt: "你是专业的企业信息提取专家..."
extract_user_template: |
  目标企业：{target}

  需要提取的字段：
  {field_descriptions}

  以下是从 {count} 个不同网页获取的正文内容：

  {item_contents}

  请从以上所有网页正文中提取上述字段的值。

# ── 报告选项 ──────────────────────────────────────────────────
report_options:
  include_sources: true
  include_checklist: true
  max_sources_per_dimension: 5

# ── 批量模式 ──────────────────────────────────────────────────
batch:
  company_concurrency: 1             # 并行处理企业数（1-10）
  continue_on_company_error: true    # 单企业失败不中止整批
  skip_existing: true
  batch_runs_dir: "batch_runs"

# ── 合并报告提示词 ────────────────────────────────────────────
merge_prompt: |
  请综合以下维度摘要，生成"{target}"的尽调报告...
  {summaries}

# ── 维度定义（可增删改）───────────────────────────────────────
dimensions:
  - id: basic_info
    name: 工商基本信息
    order: 10
    enabled: true
    required: true
    fetch_enabled: true               # 启用 crawl4ai 全文抓取
    minimax_queries:
      - '"{target}" 官方网站'
    metaso_queries:
      - "{target} 企查查 site:qcc.com"
    metaso_mode: search               # chat | search
    metaso_search_size: 1             # search 模式每查询返回网页数（1-10）
    extract_fields:                   # 结构化字段提取（不配则跳过）
      - field_name: 统一社会信用代码
        description: "18位字母数字组合"
        examples: "91440605682473330H"
      - field_name: 法定代表人
        description: "法定代表人姓名"
      - field_name: 注册资本
        description: "金额+币种，如1000万元人民币"
    summary_prompt: |
      请从以下搜索结果中提取"{target}"的工商基本信息...
      {results}
```

### 新增维度

在 `dimensions:` 下追加新条目即可（`id` 需全局唯一，`order` 控制排序），无需修改管道代码：

```yaml
  - id: supply_chain
    name: 供应链与客户
    order: 90
    enabled: true
    required: false
    minimax_queries:
      - "{target} 主要客户 供应商"
      - "{target} 上下游"
    summary_prompt: |
      请从以下搜索结果中提取"{target}"的供应链和客户信息。
      {results}
```

如需结构化提取，添加 `extract_fields` 即可：

```yaml
    extract_fields:
      - field_name: 主要客户
        description: "客户公司名称"
      - field_name: 主要供应商
        description: "供应商公司名称"
```

---

## 产物文件

每次运行在 `runs/{run_id}/` 下生成：

| 文件 | 内容 |
|------|------|
| `final_report.md` | 最终尽调报告（Markdown） |
| `dimension_summaries.json` | 各维度的结构化摘要（含可信度、待核实事项） |
| `raw_search_results.json` | 全部原始搜索结果（含 SearchItem 和 extractions） |
| `run_meta.json` | 运行元数据（run_id、状态、失败维度、时间戳、API 成本） |

`raw_search_results.json` 中每个维度包含 `extractions` 字段（若配置了 `extract_fields`）：

```json
{
  "basic_info": {
    "items": [...],
    "extractions": {
      "extractions": {
        "统一社会信用代码": [
          {
            "source_item_id": "b71f82f6ae32",
            "source_url": "https://m.liepin.com/company/12440155/",
            "value": "91440605682473330H",
            "confidence": "高"
          }
        ]
      }
    }
  }
}
```

批量运行额外生成 `batch_summary.md`、`batch_summary.csv`、`batch_meta.json`。

---

## 成本计量

每次运行结束后，stderr 输出 API 消耗摘要，同时写入 `run_meta.json`：

```
本次调用成本：
   MiniMax Search: 1 次
   LLM 推理: 4 次，tokens: 42,284
   Metaso: 1 次成功，0 次失败，credits: 6
```

| API | 计费方式 |
|-----|---------|
| MiniMax Search | 按查询次数 |
| Metaso chat | 6 credits / 查询 |
| Metaso search | 6 × size credits / 查询 |
| LLM 推理 | 按 token（输入+输出） |

---

## 可信度评级

两级可信度保障：

### 程序规则（硬性，LLM 不可突破）

| 条件 | 最高可信度 |
|------|:---:|
| 搜索结果为 0，或维度状态为 `failed` | 待核实（强制） |
| 仅 1 条搜索结果 | 低（上限） |
| 所有结果均无 URL | 低（上限） |

### 结构化提取可信度（字段级）

| 等级 | 标准 |
|:---:|------|
| 高 | 政府网站（gov.cn）或工商企业信息网站（企查查/天眼查/启信宝）明确列出 |
| 中 | 商业网站（招聘平台、行业网站、公司官网）明确列出 |
| 低 | 侧面提及、关联信息推断、第三方引用 |

最终报告中 AI 判定的可信度不得高于程序规则设定的上限。

---

## 退出码

| 退出码 | 含义 |
|:---:|------|
| 0 | 成功 |
| 1 | 管道整体失败或参数错误 |
| 2 | 必要维度失败，报告不完整 |

---

## 技术栈

| 组件 | 用途 |
|------|------|
| **Python 3.12+** | 运行时 |
| **LangGraph** | 管道编排（fan-out / fan-in） |
| **Pydantic v2** | 数据模型与配置校验 |
| **pydantic-settings** | 环境变量加载与 SM4 解密 |
| **httpx** | 异步 HTTP 客户端 |
| **crawl4ai** | 浏览器自动化全文抓取（AsyncWebCrawler） |
| **structlog** | 结构化日志 |
| **OpenAI SDK** | LLM 推理（兼容任意 OpenAI 兼容 API） |
| **PyYAML** | config.yaml 解析 |
| **uv** | 包管理与环境隔离 |
| **Ruff** | 代码检查与格式化 |
| **Mypy** | 严格模式类型检查 |
| **pytest** | 测试框架 |

---

## 架构模式

### 管道模式

7 节点有向图：`init → route → [search+summarize × N] → collect → merge → save`。LangGraph `Send` API 实现扇出，自定义 reducer（`merge_dicts`、`merge_cost`、`keep_nonempty_str`）在扇入时归并各分支结果。

### 三层并发控制

| 层级 | 控制变量 | 实现机制 |
|------|----------|----------|
| 维度间并行 | `dimension_concurrency` | LangGraph `max_concurrency` |
| 维度内查询并行 | `query_concurrency_per_dimension` | `asyncio.Semaphore` |
| 公司间并行（批处理） | `company_concurrency` | `asyncio.Semaphore` |

### 检索策略模式

三层检索（MiniMax / Metaso / crawl4ai）各自独立封装，可从配置任意组合启用或关闭：

| 维度配置 | 效果 |
|---------|------|
| 仅 `minimax_queries` | 纯搜索摘要 → LLM 提取 |
| + `metaso_queries` | 搜索 + 秘塔 AI 增强 |
| + `fetch_enabled: true` | 搜索 + crawl4ai 全文抓取 |
| + `extract_fields` | 搜索 + 抓取 + 结构化字段提取 |

### 配置分层

| 层级 | 载体 | 内容 |
|------|------|------|
| 静态 / 管道配置 | `config.yaml` | 维度定义、查询词、提示词、并发参数 |
| 运行时 / 机密 | `.env` | API 密钥（SM4 密文存储）、模型选择、Base URL |

---

## 容错与降级

管道设计容忍部分失败，非全有或全无：

| 故障场景 | 降级策略 |
|----------|----------|
| 单条搜索查询超时 | 标记维度 `status=partial`，继续处理 |
| Metaso API 不可用 | 静默回退至 MiniMax-only 结果 |
| crawl4ai 全文抓取失败 | item 保持原有 snippet，不影响后续流程 |
| 结构化提取 LLM JSON 解析失败 | 自动重试 1 次，仍失败则回退为原文直接 summarize |
| LLM summarize JSON 解析失败 | 自动重试 1 次（附带纠错提示词），仍失败回退为原始摘要拼接 |
| LLM 引用不存在的搜索结果 ID | `summarize_node` 硬过滤，标记警告 |
| 必要维度（basic_info）失败 | `required_failed=true`，退出码 2 |
| 批处理中单个企业失败 | `continue_on_company_error=true` 时跳过继续 |

---

## CI/CD

GitHub Actions（`.github/workflows/ci.yml`），每次 push 或 PR 触发：

1. `uv sync --frozen`
2. Ruff 格式化检查 + Lint
3. Mypy 严格模式类型检查
4. pytest 全量测试 + 覆盖率报告

---

## 项目结构

```
main.py                          # CLI 入口（argparse）
config.yaml                      # 应用配置
src/diligence/
├── config.py                    # AppConfig、Dimension、ExtractField、load_config()
├── models.py                    # SearchItem、DimensionSearchResult、DimensionSummary、CostRecord 等
├── settings.py                  # pydantic-settings（MINIMAX_*、METASO_*、LLM_*）
├── keys.py                      # API 密钥 SM4 加密工具
├── state.py                     # DiligenceState TypedDict 及自定义 reducer
├── graph.py                     # LangGraph 管道组装及 run_company_graph()
├── batch.py                     # 批量编排，复用 run_company_graph()
├── nodes/
│   ├── init_node.py             # 生成 run_id，初始化状态
│   ├── route_node.py            # Send API 扇出
│   ├── search_node.py           # MiniMax + Metaso + crawl4ai
│   ├── summarize_node.py        # 结构化提取 + LLM 摘要（含 JSON 重试）
│   ├── collect_node.py          # 扇入完整性检查
│   ├── merge_node.py            # LLM 合并最终报告
│   └── save_node.py             # 产物写入及成本打印
└── utils/
    ├── minimax_search.py        # MiniMax Search API 封装
    ├── metaso.py                # 秘塔 AI 搜索客户端（chat + search 模式）
    └── fetch.py                 # crawl4ai 全文抓取
tests/
├── conftest.py                  # 共享 fixtures
├── test_fetch.py                # 抓取过滤与 enrich 逻辑
├── test_metaso.py               # 秘塔客户端与数据转换
├── test_minimax_search.py       # MiniMax 搜索与去重
├── test_summarize_helpers.py    # 提取/格式化/渲染辅助函数
├── test_nodes.py                # 节点集成测试
├── test_graph.py                # 管道集成测试
├── test_batch.py                # 批量编排测试
├── test_cli.py                  # CLI 参数解析测试
├── test_config.py               # 配置加载与校验
├── test_settings.py             # 环境变量加密与解密
└── ...
```

---

## 开发

```bash
uv run pytest -q             # 运行测试（181 个）
uv run ruff check .          # Lint
uv run ruff format .         # 格式化
uv run mypy src/             # 类型检查
```
