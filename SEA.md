# SEA: Web 查询过度触发

## 现象

`uv run xft recommend --with-web <企业>` 时，阶段 2（业务 Web 证据）会规划出 **70+ 个搜索查询**，大部分来自 `rule` 和 `hybrid` 指标——这些指标本应被本地结构化证据满足，但仍触发了 Web 搜索。

## 当前规模

| 指标类型 | 数量 | Web 搜索触发条件 | 实际触发 |
|----------|------|-----------------|---------|
| `llm_web` | 43 | `when=always` | 全部 |
| `rule` | 16 | `when=rule_not_matched` | 全部（规划阶段 rule_result=None） |
| `hybrid` | 14 | `when=insufficient` | 大部分（本地证据不全） |
| `llm` | 15 | 无 web_search | 0 |

## 根因

`web_policy.py:49` — `should_search_indicator()` 在规划阶段被调用时，rule 评估尚未执行（处于 pipeline 的更后阶段），`rule_result` 始终为 `None`：

```python
if when == "rule_not_matched":
    if rule_result in ("not_matched", "unknown", None):  # ← None 全部命中
        return WebSearchDecision(enabled=True, ...)
```

这导致所有 `rule` 指标的搜索请求全部执行，即使后续 rule 评估完全可以命中本地证据。

## 搜索量构成（72 个查询）

```
43 llm_web × 1~3 fixed_queries  ≈ 43~50
16 rule    × 1~2 fixed_queries  ≈ 16~20  ← 大部分冗余
14 hybrid  × 1 fixed_query      ≈ 14     ← 部分冗余
 2 auto-generated                ≈ 2
─────────────────────────────────────────
合计                               72
```

每条查询还会通过 crawl4ai 抓取结果页面的完整内容（`fetch.enabled=true`），进一步放大延迟和 API 消耗。

## 影响

- **延迟**：72 次搜索 + 页面抓取，阶段 2/4 停顿时间远超用户预期
- **API 额度浪费**：MiniMax/Metaso credits + LLM token 被无效查询消耗
- **证据噪声**：冗余查询拉回低质量结果混入 evidence，LLM 判断质量可能下降

## 改进方案

### 方案 A：配置层面（短期，低风险）

逐指标审查 `modules.d/*.yaml`：

- `evaluator=rule` 且本地证据字段可以完整覆盖的指标 → 删除 `web_search` 段或设 `when: never`
- `evaluator=hybrid` 的指标 → 显式设 `when: rule_not_matched`，确保本地命中时不搜索
- `llm_web` 的指标 → 检查是否可降级为 `hybrid` 或 `llm`（如本地已有足够数据源）

预计可将查询数从 72 降到 30-40。

### 方案 B：策略层面（中期，需改 pipeline）

在 `_plan_web_queries()` 之前，先执行一轮 rule 评估（`rule` + `hybrid` 中的 rule 部分），拿到真实的 `rule_result`，再传入 `should_search_indicator()`。

这样：
- `rule_not_matched` 条件可以正确区分配置了 web_search 但本地已命中的 rule 指标
- 规划阶段不再盲目触发所有 rule 搜索

改动点：
1. `web_evidence.py:_plan_web_queries()` — 在遍历 indicators 前加载 rule 结果
2. `web_policy.py:should_search_indicator()` — 移除 `None` 的宽松容错（改为显式传入 `rule_result`）

### 方案 C：两者结合（推荐）

先执行方案 A 快速收敛，再实施方案 B 从根本上消除冗余，最终使查询量与真正需要 Web 补证的指标数对齐。
