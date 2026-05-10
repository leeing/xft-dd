# xft-dd · 企业尽调自动化工具

基于三层检索增强与 AI 推理的企业尽职调查工具。输入企业名称，自动生成覆盖 8 个维度的结构化尽调报告。

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

# ── 秘塔 AI 搜索（可选，启用后补充精准问答）─────────────────────
METASO_API_KEY=
METASO_ENABLED=false

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
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph Pipeline                                              │
│                                                                  │
│  init_node                                                       │
│    → 生成 run_id，过滤已启用维度，创建输出目录                  │
│    │                                                             │
│    ▼  (Send API 扇出，受 dimension_concurrency 限制)            │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  search_node     │  │  search_node     │  │  ...×8        │  │
│  │  (basic_info)    │  │  (tech_cert)     │  │               │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬────────┘  │
│           ▼                     ▼                    ▼            │
│  summarize_node × 8（search 后立即 summarize，维度内串行）       │
│    │                                                             │
│    ▼  (扇入，自定义 reducer 归并结果)                            │
│  collect_node  → 检查必要维度完整性                              │
│    │                                                             │
│    ▼                                                             │
│  merge_node    → LLM 合并 8 个维度摘要，生成 Markdown 报告      │
│    │                                                             │
│    ▼                                                             │
│  save_node     → 写入产物文件，打印成本摘要                      │
└─────────────────────────────────────────────────────────────────┘
  │
  ▼
输出: runs/{run_id}/
  ├── final_report.md
  ├── dimension_summaries.json
  ├── raw_search_results.json
  └── run_meta.json              ← 含本次 API 调用成本
```

---

## 三层检索增强

### 第一层：MiniMax Search — 宽覆盖网页检索（不可替换）

**端点**：`POST /v1/coding_plan/search`  
**实现**：`src/diligence/utils/minimax_search.py`

- 对每个维度的 `minimax_queries` 并发发起搜索，受 `query_concurrency_per_dimension` 限流
- 返回网页列表（title + link + snippet），每查询最多 `max_results_per_query` 条
- 覆盖企查查、天眼查、招聘平台、新闻、政府公告等多类来源
- 多条查询结果自动去重：URL 优先匹配，无 URL 时降级为 title+snippet 匹配

### 第二层：Metaso 秘塔 AI — 精准事实问答（可选）

**端点**：`POST https://metaso.cn/api/v1/chat/completions`  
**实现**：`src/diligence/utils/metaso.py`  
**启用方式**：`.env` 中设置 `METASO_ENABLED=true` 并填入 `METASO_API_KEY`

- 对每个维度发起 1-3 条自然语言问答（例如「{target}的统一社会信用代码是什么？」）
- 秘塔内部联网检索企查查、启信宝及工商数据库，返回 AI 合成的自然语言答案
- 答案包装为带 `full_text` 的 SearchItem，插入对应维度结果列表最前端，确保 LLM 优先采信
- 响应中的 `credits` 消耗自动累计，运行结束后统一展示

> 公司名含 ASCII 括号（如 `美世乐(广东)新能源科技有限公司`）时，自动转换为全角 `（）` 再发送至秘塔，避免查询解析歧义。MiniMax Search 不受影响（使用引号包裹查询词）。

### 第三层：Playwright — 全文抓取增强（可选，默认关闭）

**实现**：`src/diligence/utils/fetch.py`  
**启用方式**：在 `config.yaml` 的目标维度下设置 `fetch_enabled: true`，且 `fetchable_domains` 白名单中包含目标域名片段

- 仅抓取 URL 匹配 `fetchable_domains` 白名单的页面，未匹配则跳过
- 通过真实浏览器访问目标页面，提取完整正文以替换 100-200 字的搜索摘要
- 适合工商注册详情页、CNIPA 专利页等结构化内容页面
- 不适合需要登录的平台（天眼查、招聘网站等）

### 推理层：LLM — 结构化摘要与报告生成（模型可替换）

**端点**：任意 OpenAI 兼容 API  
**实现**：`src/diligence/nodes/summarize_node.py`、`src/diligence/nodes/merge_node.py`

- **summarize_node**：读取各维度检索结果，输出结构化 JSON，包含可信度评级、待核实项及证据 ID
- **merge_node**：合并 8 个维度摘要，生成最终 Markdown 报告及人工核验清单
- 通过 `.env` 中的 `LLM_*` 变量切换模型，无需修改代码

### 检索策略分工

| 任务 | 使用层级 | 理由 |
|------|---------|------|
| 发现「企业出现在哪些网页」 | MiniMax Search | 覆盖面广，各类网站均可命中 |
| 查询「统一社会信用代码」等精确事实 | Metaso 秘塔 | 直接检索工商数据库，答案精准 |
| 获取详情页完整正文 | Playwright | 突破搜索摘要的字数限制 |
| 裁决「2003 年还是 2008 年成立」等冲突信息 | LLM | 需要跨来源推理 |
| 输出带可信度标注的结构化摘要 | LLM | 需要格式控制与语义理解 |

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

`required=true` 的维度（当前仅 `basic_info`）失败时，`run_meta.json` 中 `required_failed` 标记为 `true`，进程退出码为 2。

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

# ── Playwright 参数（仅在维度设置 fetch_enabled: true 时生效）──
playwright_fetch_timeout: 25         # 单页抓取超时（秒，5-120）
playwright_fetch_concurrency: 2      # 并行抓取数（1-5）

# ── 可抓取域名白名单 ──────────────────────────────────────────
# URL 中包含任意一项即触发 Playwright 全文抓取
# 留空则即使 fetch_enabled: true 也不会抓取任何页面
fetchable_domains: []
# 示例：
#   fetchable_domains:
#     - "example.com"
#     - "anothersite.cn"

# ── 报告选项 ──────────────────────────────────────────────────
report_options:
  include_sources: true
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
    order: 10                         # 排序，数值越小越靠前
    enabled: true
    required: true
    fetch_enabled: false
    minimax_queries:                  # MiniMax Search 查询词（{target} 自动替换）
      - "{target} 工商注册信息"
      - "{target} 统一社会信用代码"
    metaso_queries:                   # 秘塔 AI 问答（{target} 自动替换）
      - "{target}的统一社会信用代码、注册资本、成立时间、注册地址是什么？"
    summary_prompt: |                 # 维度摘要提示词（{target} 和 {results} 自动替换）
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

---

## 产物文件

每次运行在 `runs/{run_id}/` 下生成：

| 文件 | 内容 |
|------|------|
| `final_report.md` | 最终尽调报告（Markdown） |
| `dimension_summaries.json` | 8 个维度的结构化摘要（含可信度、待核实事项） |
| `raw_search_results.json` | 全部原始搜索结果（含秘塔 SearchItem） |
| `run_meta.json` | 运行元数据（run_id、状态、失败维度、时间戳、API 成本） |

批量运行额外生成 `batch_summary.md`、`batch_summary.csv`、`batch_meta.json`。

---

## 成本计量

每次运行结束后，stderr 输出 API 消耗摘要，同时写入 `run_meta.json`：

```
本次调用成本：
   MiniMax Search: 18 次
   LLM 推理: 9 次，tokens: 42,300
   Metaso: 12 次，credits: 36
```

`run_meta.json` 中的 `cost` 字段（机器可读）：

```json
"cost": {
  "minimax_search_calls": 18,
  "llm_calls": 9,
  "llm_tokens_total": 42300,
  "metaso_calls": 12,
  "metaso_credits_total": 36
}
```

---

## 可信度评级

可信度由程序强制规则兜底，AI 不得自行提升等级：

| 条件 | 最高可信度 |
|------|:---:|
| 搜索结果为 0，或维度状态为 `failed` | 待核实（强制） |
| 仅 1 条搜索结果 | 低（上限） |
| 所有结果均无 URL | 低（上限） |
| 其他情况 | 由 AI 判定（高 / 中 / 低 / 待核实） |

---

## 退出码

| 退出码 | 含义 |
|:---:|------|
| 0 | 成功 |
| 1 | 管道整体失败或参数错误 |
| 2 | 必要维度（basic_info）失败，报告不完整 |
| 3 | 批量模式：至少一家企业失败 |

---

## 技术栈

| 组件 | 用途 |
|------|------|
| **Python 3.12+** | 运行时 |
| **LangGraph** | 管道编排（fan-out / fan-in） |
| **Pydantic v2** | 数据模型与配置校验 |
| **pydantic-settings** | 环境变量加载与 SM4 解密 |
| **httpx** | 异步 HTTP 客户端 |
| **Playwright** | 浏览器自动化全文抓取 |
| **structlog** | 结构化日志 |
| **OpenAI SDK** | LLM 推理（兼容任意 OpenAI 兼容 API） |
| **PyYAML** | config.yaml 解析 |
| **uv** | 包管理与环境隔离 |
| **Ruff** | 代码检查与格式化 |
| **Mypy** | 严格模式类型检查 |
| **pytest** | 测试框架 |

## 代码规模

| 类别 | 文件数 | 代码行数 |
|------|:---:|:---:|
| 核心源码 (`src/diligence/`) | 16 | ~2,000 |
| CLI 入口 (`main.py`) | 1 | ~180 |
| 配置 (`config.yaml`) | 1 | ~300 |
| 测试 (`tests/`) | 14 | ~2,600 |
| **测试/源码比** | | **1.17:1** |

## 架构模式

### 管道模式

7 节点有向图：`init → route → [search+summarize × N] → collect → merge → save`。LangGraph `Send` API 实现扇出，自定义 reducer（`merge_dicts`、`merge_cost`、`keep_nonempty_str`）在扇入时归并各分支结果。

### 三层并发控制

| 层级 | 控制变量 | 实现机制 |
|------|----------|----------|
| 维度间并行 | `dimension_concurrency` | LangGraph `max_concurrency` |
| 维度内查询并行 | `query_concurrency_per_dimension` | `asyncio.Semaphore` |
| 公司间并行（批处理） | `company_concurrency` | `asyncio.Semaphore`，硬上限 50 |

### 策略模式

三层检索（MiniMax Search / Metaso / Playwright）各自独立封装，可任意组合启用或关闭，管道代码无需变更。

### 单例模式

- LLM 客户端（`AsyncOpenAI`）：首次调用创建，全局复用
- Playwright 浏览器实例：懒加载持久化 Chromium，避免反复启动开销

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
| LLM JSON 解析失败 | 自动重试（附带纠错提示词），仍失败则回退为原始摘要 |
| 必要维度（basic_info）失败 | `required_failed=true`，退出码 2 |
| 批处理中单个企业失败 | `continue_on_company_error=true` 时跳过继续 |
| LLM 引用不存在的搜索结果 ID | `summarize_node` 硬过滤，标记为可疑项 |

---

## CI/CD

GitHub Actions（`.github/workflows/ci.yml`），每次 push 或 PR 触发：

1. `uv sync --frozen`
2. Ruff 格式化检查 + Lint
3. Mypy 严格模式类型检查
4. pytest 全量测试 + 覆盖率报告
5. HTML 覆盖率报告作为 artifact 上传

---

## 项目结构

```
main.py                     # CLI 入口（argparse）
config.yaml                 # 应用配置
src/diligence/
├── config.py               # AppConfig、Dimension、load_config()
├── models.py               # SearchItem、DimensionSummary、RunMeta、CostRecord 等
├── settings.py             # pydantic-settings（MINIMAX_*、METASO_*、LLM_*）
├── keys.py                 # API 密钥 SM4 加密工具
├── state.py                # DiligenceState TypedDict 及自定义 reducer
├── graph.py                # LangGraph 管道组装及 run_company_graph()
├── batch.py                # 批量编排，复用 run_company_graph()
├── nodes/
│   ├── init_node.py        # 生成 run_id，初始化状态
│   ├── route_node.py       # Send API 扇出
│   ├── search_node.py      # MiniMax + 秘塔 + Playwright（可选）
│   ├── summarize_node.py   # LLM 结构化摘要（含 JSON 重试）
│   ├── collect_node.py     # 扇入完整性检查
│   ├── merge_node.py       # LLM 合并最终报告
│   └── save_node.py        # 产物写入及成本打印
└── utils/
    ├── minimax_search.py   # MiniMax Search API 封装
    ├── metaso.py           # 秘塔 AI 搜索客户端
    └── fetch.py            # Playwright 全文抓取
tests/
├── conftest.py             # 共享 fixtures
├── test_graph.py           # 管道集成测试
├── test_nodes.py           # 节点单元测试
├── test_batch.py           # 批量编排测试
├── test_cli.py             # CLI 参数解析测试
└── ...                     # 其他模块测试
```

---

## 开发

```bash
uv run pytest -q             # 运行测试
uv run ruff check .          # Lint
uv run ruff format .         # 格式化
uv run mypy src/             # 类型检查
```
