# xft-dd · 企业尽调自动化工具

自动化的中国制造业企业尽职调查工具。输入企业名称，通过三层检索增强 + AI 推理，生成覆盖 8 个维度的结构化尽调报告。

---

## 快速开始

```bash
# 复制并填写凭证
cp .env.example .env

# 单企业尽调
uv run main.py "佛山市固特家居制品有限公司"

# 只跑特定维度
uv run main.py "某公司" --only basic_info,tech_cert

# 跳过某维度
uv run main.py "某公司" --skip listing

# 预览搜索词，不发起真实请求
uv run main.py "某公司" --dry-run

# 批量（txt 或 csv）
uv run main.py --batch companies.txt
uv run main.py --batch companies.csv --name-column company_name

# 批量续跑（跳过已完成的企业）
uv run main.py --batch companies.txt --resume --batch-dir batch_runs/20260510-...
```

---

## 环境配置（.env）

复制 `.env.example`，按需填写：

```env
# ── 搜索层（不可替换）──────────────────────────────────────────
MINIMAX_API_KEY=your_minimax_api_key_here
MINIMAX_BASE_URL=https://api.minimaxi.chat/v1

# 秘塔 AI 搜索（可选，启用后补充精准问答）
METASO_API_KEY=
METASO_ENABLED=false

# ── 推理层（可替换为任意 OpenAI 兼容模型）──────────────────────
# 不填 LLM_API_KEY 则自动复用 MINIMAX_API_KEY
LLM_API_KEY=
LLM_BASE_URL=https://api.minimaxi.chat/v1
LLM_MODEL=MiniMax-M2.7-Highspeed

# 使用 DeepSeek 示例：
# LLM_API_KEY=sk-xxx
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_MODEL=deepseek-chat
```

---

## 整体架构

```
输入: 企业名称
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph Pipeline                                              │
│                                                                  │
│  init_node                                                       │
│    → 生成 run_id，过滤 enabled 维度，创建输出目录               │
│    │                                                             │
│    ▼  (Send API 扇出，最多 dimension_concurrency=5 并发)        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  search_node     │  │  search_node     │  │  ...×8        │  │
│  │  (basic_info)    │  │  (tech_cert)     │  │               │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬────────┘  │
│           ▼                     ▼                    ▼            │
│  summarize_node × 8（search 完成后立即 summarize，维度内串行）   │
│    │                                                             │
│    ▼  (扇入)                                                     │
│  collect_node  → 检查必要维度完整性                              │
│    │                                                             │
│    ▼                                                             │
│  merge_node    → AI 合并 8 个摘要 → markdown 报告               │
│    │                                                             │
│    ▼                                                             │
│  save_node     → 写入 4 个产物文件，打印成本摘要                │
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

## 三层 API 分工

### 第一层：MiniMax Search — 宽覆盖网页检索（不可替换）

**端点**：`POST /v1/coding_plan/search`  
**实现**：`src/diligence/utils/minimax_search.py`

- 对每个维度的每条 `minimax_queries` 并发发起搜索（受 `query_concurrency_per_dimension` 限流）
- 返回网页列表（title + link + snippet），最多 `max_results_per_query` 条/query
- 覆盖面广：企查查、天眼查、招聘平台、新闻、政府公告都能命中
- 多条查询结果自动去重（URL 优先，无 URL 则按 title+snippet）

### 第二层：Metaso 秘塔 AI — 精准事实问答（可选）

**端点**：`POST https://metaso.cn/api/v1/chat/completions`  
**实现**：`src/diligence/utils/metaso.py`  
**启用方式**：`.env` 中设 `METASO_ENABLED=true` 并填入 `METASO_API_KEY`

- 对每个维度发起 1-3 条自然语言问答，直接问「{target}的统一社会信用代码是什么？」
- 秘塔内部联网检索企查查、启信宝、工商数据库，返回 AI 合成的自然语言答案
- 答案被包装为带 `full_text` 的 SearchItem，**插在该维度结果列表最前面**，确保 LLM 优先采信
- 响应中包含 `credits` 消耗，自动累计并在运行结束后展示

> 公司名含 ASCII 括号（如 `美世乐(广东)新能源科技有限公司`）时，自动转换为全角 `（）` 再发给秘塔，避免查询解析器歧义。MiniMax Search 不受影响（使用引号查询）。

### 第三层：Playwright — 全文抓取增强（可选，默认关闭）

**实现**：`src/diligence/utils/fetch.py`  
**启用方式**：在 `config.yaml` 某个维度下设 `fetch_enabled: true`

- 对该维度的搜索结果 URL 发起真实浏览器访问，提取完整正文（替换 100-200 字摘要）
- 适合：工商注册详情页、CNIPA 专利页等结构化内容页面
- 不适合：需要登录的平台（天眼查、招聘网站）

### 推理层：LLM — 结构化摘要与报告（可替换）

**端点**：任意 OpenAI 兼容 API  
**实现**：`src/diligence/nodes/summarize_node.py`、`src/diligence/nodes/merge_node.py`

- **summarize_node**：读取每个维度的检索结果，输出结构化 JSON（含可信度评级、待核实项、证据 ID）
- **merge_node**：把 8 个维度摘要合并为最终 markdown 报告，含人工核验清单
- 通过 `.env` 的 `LLM_*` 变量替换，无需改代码

---

## 为什么这样分工

| 任务 | 用哪层 | 理由 |
|------|--------|------|
| 找「公司出现在哪些网页」 | MiniMax Search | 宽覆盖，各类网站都能找到 |
| 找「统一社会信用代码是多少」 | Metaso 秘塔 | 直接查工商数据库，精准答案 |
| 获取详情页完整内容 | Playwright | 突破摘要字数限制 |
| 裁决「2003年还是2008年成立」 | LLM | 跨来源推理，需要大模型 |
| 输出带可信度的结构化摘要 | LLM | 格式控制，需要大模型 |

---

## 8 个尽调维度

| 维度 ID | 名称 | required |
|---------|------|:---:|
| `basic_info` | 工商基本信息 | ✅ |
| `industry` | 行业与细分 | |
| `scale` | 员工规模 | |
| `background` | 企业背景 | |
| `tech_cert` | 科技属性资质 | |
| `ip` | 知识产权 | |
| `product` | 产品与定位 | |
| `listing` | 上市情况 | |

`required=true` 的维度（目前只有 `basic_info`）失败时，`run_meta.json` 中 `required_failed=true`，进程退出码为 2。

---

## config.yaml 配置说明

```yaml
schema_version: "1.0"

# 并发控制
dimension_concurrency: 5          # 最多同时处理几个维度（1-20）
query_concurrency_per_dimension: 2 # 每个维度内最多并发几个搜索请求（1-5）
search_timeout_seconds: 60        # 单次搜索超时（秒）
max_results_per_query: 10         # 每条查询最多取多少结果

# 输出
output_language: "zh-CN"
runs_dir: "runs"                  # 单企业产物目录根

# AI 角色提示词（可按行业定制，不改代码）
summarize_system_prompt: "你是中国制造业企业尽调专家..."
merge_system_prompt: "你是一个中国制造业行业顶级专家..."

# Playwright 参数（仅在维度开启 fetch_enabled: true 时生效）
playwright_fetch_timeout: 25      # 单页抓取超时（秒，5-120）
playwright_fetch_concurrency: 2   # 并发抓取数（1-5）

# 报告选项
report_options:
  include_sources: true
  max_sources_per_dimension: 5

# 批量模式
batch:
  company_concurrency: 1          # 同时处理几家企业（1-10）
  continue_on_company_error: true # 单企业失败不中止整批
  skip_existing: true
  batch_runs_dir: "batch_runs"

# 合并报告提示词
merge_prompt: |
  请综合以下维度摘要，生成"{target}"的尽调报告...
  {summaries}

# 维度定义（可增删改，不改代码）
dimensions:
  - id: basic_info
    name: 工商基本信息
    order: 10             # 排序，越小越靠前
    enabled: true
    required: true        # 失败时触发 exit(2)
    fetch_enabled: false  # 是否启用 Playwright 全文抓取
    minimax_queries:      # MiniMax Search 查询词（{target} 自动替换）
      - "{target} 工商注册信息"
      - "{target} 统一社会信用代码"
    metaso_queries:       # 秘塔 AI 问答（{target} 自动替换，需 METASO_ENABLED=true）
      - "{target}的统一社会信用代码、注册资本、成立时间、注册地址是什么？"
    summary_prompt: |     # 维度摘要提示词（{target} 和 {results} 自动替换）
      请从以下搜索结果中提取"{target}"的工商基本信息...
      {results}
```

### 动态新增维度

只需在 `dimensions:` 下追加一个新条目（`id` 全局唯一，`order` 控制顺序），无需改代码：

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

## 成本计量

每次运行结束后，stderr 输出本次 API 消耗摘要，同时写入 `run_meta.json`：

```
💰 本次调用成本：
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

## 项目结构

```
src/diligence/
├── config.py               # AppConfig、Dimension、load_config()
├── models.py               # SearchItem、DimensionSummary、RunMeta、CostRecord 等
├── settings.py             # BaseSettings（MINIMAX_*、METASO_*、LLM_*）
├── state.py                # DiligenceState TypedDict + reducers
├── graph.py                # LangGraph 组装 + run_company_graph()
├── batch.py                # 批量编排，复用 run_company_graph()
├── nodes/
│   ├── init_node.py        # 生成 run_id，初始化状态
│   ├── route_node.py       # Send API 扇出
│   ├── search_node.py      # MiniMax Search + 秘塔 + Playwright（可选）
│   ├── summarize_node.py   # AI 结构化摘要（含 JSON 重试）
│   ├── collect_node.py     # 扇入完整性检查
│   ├── merge_node.py       # AI 合并最终报告
│   └── save_node.py        # 写入产物文件 + 打印成本摘要
└── utils/
    ├── minimax_search.py   # MiniMax Search API 封装
    ├── metaso.py           # 秘塔 AI 搜索客户端
    └── fetch.py            # Playwright 全文抓取（可选增强）
```

---

## 产物文件说明

每次运行在 `runs/{run_id}/` 下生成：

| 文件 | 内容 |
|------|------|
| `final_report.md` | 最终尽调报告（markdown） |
| `dimension_summaries.json` | 8 个维度的结构化摘要（含可信度、待核实事项） |
| `raw_search_results.json` | 全部原始搜索结果（含秘塔 SearchItem） |
| `run_meta.json` | 运行元数据（run_id、状态、失败维度、时间戳、API 成本） |

批量运行额外生成 `batch_summary.md`、`batch_summary.csv`、`batch_meta.json`。

---

## 可信度评级规则

可信度由程序逻辑强制兜底，AI 不能自由升级：

| 条件 | 最高可信度 |
|------|-----------|
| 搜索结果为 0 条，或维度状态 `failed` | 待核实（强制） |
| 仅 1 条搜索结果 | 低（上限） |
| 所有结果均无 URL | 低（上限） |
| 其他情况 | 由 AI 判断（高 / 中 / 低 / 待核实） |

---

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 管道整体失败 / 参数错误 |
| 2 | 必要维度（basic_info）失败，报告不完整 |
| 3 | 批量模式：至少一家企业失败 |

---

## 开发

```bash
uv run pytest -q               # 运行测试
uv run ruff check .            # Lint
uv run ruff format .           # 格式化
uv run mypy src/               # 类型检查
```
