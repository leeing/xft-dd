# 技术债

本文档只记录当前推荐主线仍值得处理的维护项。

## 推荐流水线

- `WebResolver` 已经改为按指标 lazy 触发。后续若发现 Web 查询仍偏多，优先治理配置里的 `when: always` 和泛查询词，而不是新增独立 Web 节点。
- evaluator 仍承担 rule、LLM、hybrid、Web resolver 编排。短期保持集中实现，避免过早拆出多套抽象；当某类 evaluator 的测试或变更明显膨胀时再拆。
- `rule` + `possible_on_evidence` 的语义需要在配置评审中持续约束：Web 只能补成 `possible`，不能直接替代结构化规则命中。

## 配置治理

- `modules.d/*.yaml` 是业务调优的主要入口。新增指标前先判断能否用 `data_sources` 或 `rule` 表达，只有确实需要文本推理时再使用 `llm` / `hybrid`。
- `llm_web` 应只用于必须依赖公开网页的指标；如果 DuckDB 或本地明细表已经能覆盖，应改回 `rule` 或 `hybrid`。
- 所有 `fixed_queries` 都应包含指标词，避免只搜索公司官网、新闻、介绍等泛页面。

## 验证缺口

- 需要持续补充真实业务标注样本，用 `xft calibrate` 跟踪 top1 命中率、可接受命中率和 Web 证据覆盖率。
- Web provider 的线上效果会随搜索服务变化，配置变更后应抽查 `web_trace.json` 与 `indicator_evidence.json`。
