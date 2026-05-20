# LLM 调用与 Web 搜索分析

## 两种调用的区别

| 维度 | Web 搜索 | LLM 指标评估 |
|------|----------|-------------|
| 阶段 | `business_web_evidence` | `business_recommend` |
| API | MiniMax Search API | MiniMax LLM (M2.7) |
| 目的 | 从互联网检索企业公开信息 | 基于证据判断指标是否命中 |
| 调用次数（崭亮实业） | **8 次**（去重后） | **78 次** |
| 去重 | ✅ 已实现 query+provider 级缓存 | 每个指标独立调用，无批处理 |
| 并发 | 串行（每 provider 逐 query 执行） | concurrency=4 并行 |

## 优化历史

### LLM 输出与 Web 证据防护（2026-05-20）

已完成的防护：

- LLM JSON 调用默认使用 `response_format={"type": "json_object"}`；若 provider 明确不支持该参数，则自动去掉参数重试。
- `_LlmIndicatorPayload.evidence` 支持 `str`、`dict`、`list[dict]` 等常见模型输出形态，减少非业务性 `ValidationError`。
- LLM 指标解析失败时返回 `unknown`，不再通过 `evidence_hints` 或 Web 噪声 fallback 成 `matched`。
- Web 搜索结果进入指标证据前必须包含目标公司名或统一社会信用代码，避免把同城/同行/相似名称企业证据错套到目标公司。
- Web 搜索结果还必须包含指标相关词，避免 BOSS 注册页、泛官网页等“公司相关但指标无关”的页面进入证据。
- `{company_name} 官网`、`{company_name} 新闻` 这类泛查询会补入指标关键词，降低重复泛搜带来的噪声。
- `llm_web` 没有实际 Web 证据时直接返回 `unknown`，不再空证据调用 LLM。
- 模块接受度增加可信度约束：如果“高”完全由中低置信、Web-only 命中堆出来，会降到“中高”。

### Web 搜索去重（2026-05-20）

优化前 130 次 HTTP 搜索（62x `{company_name} 官网` + 62x `{company_name} 新闻`），优化后 8 次。

实现：`business_web_evidence.py` 内 `_query_cache`，key 为 `query:provider`，首次执行后缓存结果，后续指标复用。

### LLM 调用：不建议合并

曾考虑将同模块多个指标合并为单次 LLM 批处理调用。**不建议**，原因：

1. **上下文污染**：MiniMax M2.7 非 GPT-4/Claude 级别模型，同时判断 5-6 个指标时注意力分散，易把指标 A 的证据错套到指标 B
2. **解析风险增加**：嵌套 JSON 输出结构更复杂，`JSONDecodeError` 概率上升
3. **收益有限**：concurrency=4 并行下，78 次调用总耗时约 2 分钟，瓶颈在 LLM 响应速度而非调用次数

### 假勤管理指标治理（2026-05-20）

已先以 `假勤管理` 作为样板模块完成配置治理：

| evaluator | 治理前 | 治理后 |
|-----------|-------:|-------:|
| rule | 4 | 10 |
| hybrid | 0 | 4 |
| llm | 3 | 2 |
| llm_web | 14 | 5 |

主要调整：

- `行业`、`分支机构数量`、`倒班制用工`、`考勤岗位设置`、`外贸岗位设置` 等改为结构化 rule。
- `跨区域经营`、`境外设厂`、`验厂相关资质 / 记录`、`境外工作地点` 改为 hybrid：本地规则先判，Web/LLM 只补充公开证据。
- `研发人员占比` 不再按无法核实的比例直接判断，改为本地招聘中的明确研发岗位线索。
- `研发费用占比` 改为公开披露型 `llm_web`，仅接受研发投入、年报、招股书等相关公开线索。
- 剩余 `llm_web` 查询由泛化的“官网/新闻”改为指标专用查询，如“生产基地 工厂”“出口营收 海外收入”“BSCI Sedex SA8000”。

### 差旅报销指标治理（2026-05-20）

`差旅报销` 作为第二个样板模块完成配置治理：

| evaluator | 治理前 | 治理后 |
|-----------|-------:|-------:|
| hybrid | 0 | 10 |
| llm_web | 13 | 3 |

主要调整：

- 有招聘、资质、知识产权等 `data_sources` 的指标改为 `hybrid` + `merge_policy: rule_first`。
- 原来空的 `text_contains.keywords` 全部补成业务关键词，避免任意招聘记录误命中。
- `web_search.fixed_queries` 从 `{company_name} 官网` / `{company_name} 新闻` 改为指标专用查询，如“售后 派驻 驻点 外勤”“境外出差 海外驻点”“海外经销商 海外代理商”。
- 仅保留确实依赖公开网页理解产品/行业/服务支持的指标为 `llm_web`。

### 推荐方向：继续减少 LLM 指标数

当前大量 `llm_web` 指标的判断质量存疑。以崭亮实业为例：

| 指标 | evaluator | 证据 | 问题 |
|------|-----------|------|------|
| 研发人员占比 | llm_web | BOSS直聘注册页 | 注册页不含研发信息，不应 matched |
| 研发费用占比 | llm_web | 库斯家具（别家公司） | 搜索返回不相关结果，误判 |
| 分支机构数量 | llm_web | BOSS直聘注册页 | profile.branch_count=0，Web 证据弱 |
| 财报合并范围 | llm_web | BOSS直聘 + 库斯家具 | 小公司无财报披露 |

建议：继续将能用本地结构化数据判断的指标改为 `rule` 或 `hybrid`（查 profile 字段或 DuckDB 表），仅保留确实需要 Web 证据的指标用 `llm_web`。

判断标准：

- **rule**：指标可通过 profile 字段（industry, labels, branch_count）、DuckDB 表（recruitments, branches, qualifications）确定 → 不用 LLM
- **llm**：本地证据文本需要理解/推理（如 business_scope 文本分析）但不需要 Web → 1 次 LLM
- **llm_web**：公开网页才可能有的信息（官网产品线、招聘页、新闻稿）→ 1 次 Web 搜索 + 1 次 LLM

当前治理进度：

| 模块 | evaluator 分布 |
|------|----------------|
| 假勤管理 | rule 10 / hybrid 4 / llm 2 / llm_web 5 |
| 差旅报销 | hybrid 10 / llm_web 3 |

后续优先治理 `日常报销`、`销项发票`，它们仍保留较多 `llm_web` 指标。

### Auto Query 生成

当前仅 `假勤管理.科技属性.细分行业` 配置了 `auto: {enabled: true, max_queries: 2}`，其余指标 `auto: false`。

Auto query 走 `_plan_auto_queries_with_llm`，额外消耗 1 次 LLM 调用生成搜索词。当前设计中 planner 调用不计入 `llm_calls.jsonl`。如需大规模启用 auto query，需考量额外的 LLM 调用成本。

## 相关文件

| 文件 | 职责 |
|------|------|
| `business_web_evidence.py` | Web 搜索执行、去重缓存、auto query 规划 |
| `business_web_policy.py` | 搜索触发策略（when/effect 决策） |
| `business_evaluator.py` | LLM 指标评估（`_evaluate_llm_indicator`） |
| `business_models.py` | `BusinessWebSearchConfig`、`_LlmIndicatorPayload` |
| `ERROR.md` | LLM 输出解析错误分析与修复 |
