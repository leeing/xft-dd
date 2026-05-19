# TECH_DEBT.md

本文档记录当前真实存在的技术债。当前优先级是正确性、可配置性和文档一致性；扩展性相关事项暂时后置。

## 当前基线

项目已经收敛为 `uv run xft <command>` 的产品化 CLI：

- `uv run xft recommend`：产品推荐流水线
- `uv run xft diligence`：企业尽调流水线
- `uv run xft calibrate`：推荐校准
- `uv run xft web enrich/import`：Web 补证和入库
- `uv run xft warehouse build`：本地 JSON 入库
- `uv run xft scenario validate/inspect`：场景配置校验和审计

当前验证结果：

- 入口帮助：通过
- `sales_recommendation` 场景校验：通过
- `bank_marketing` 场景校验：通过
- 推荐流水线离线运行：通过
- 推荐业务结果层：通过，生成业务版 `result.json`、`internal_result.json`、`business_label_result.json`
- Web 小批次验证：通过，覆盖跳过搜索、强制搜索/抓取/抽取/入库、缓存复用
- 尽调流水线 dry-run：通过
- CLI / 冒烟入口测试：`18 passed`
- 全量测试：`457 passed`
- `uv run mypy src`：通过
- `uv run ruff check src tests`：通过

## 高优先级技术债

### 1. 真实业务标注样本不足

状态：未完成。

现状：

- 校准 CLI 已可用。
- Web / LLM 链路已做过第一轮验证。
- 业务版 `result.json` 已落地，`business_modules.yaml` 已覆盖 7 个模块。
- 但缺少业务人员标注后的 5-10 家真实样本。

影响：

- 当前只能证明流水线可运行，不能证明推荐结果足够准。

建议：

1. 业务人员补 `calibration_labels.csv`。
2. 跑：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

3. 根据错配案例调整配置。

### 2. Web 结果质量仍需业务侧抽样确认

状态：未完成。

现状：

- Web 搜索、抓取、抽取、入库和缓存复用已经跑通。
- 小批次中能看到相关性过滤生效，也能看到部分搜索结果来自无关或低质量页面。

影响：

- 当前能证明 Web 链路可运行，但不能证明每条 Web 证据都足够高质量。

建议：

- 选 2-3 家企业人工检查 `dimension_analysis.json` 中的 `web_evidence`。
- 根据噪声来源调整 `analysis_dimensions.yaml` 搜索词、`web_search.yaml` blocked domains 和 Web 抽取 prompt。

### 3. `bank_marketing` 仍是示例场景，不是真实验收场景

状态：未完成。

现状：

- `config/scenarios/bank_marketing/scenario.yaml` 已验证 patch 能力。
- 但它尚未承载完整真实银行营销规则。

影响：

- “业务人员只通过配置定制场景”的目标，还需要一个真实第二场景验证。

建议：

- 用真实业务规则补全 `bank_marketing`。
- 跑相同企业，对比 `sales_recommendation` 和 `bank_marketing` 输出差异。

## 中低优先级技术债

### 1. Web / LLM 指标还没有全面进入批量质量报告

状态：部分完成。

常规推荐报告已展示业务结果，批量质量报告中对 Web cache 命中、LLM fallback、冲突数量、证据覆盖率的展示还不够统一。

当前先不急，等基础推荐质量验证后再做。

### 2. 报告文案仍偏规则模板

状态：未完成。

无 LLM 模式能稳定跑通，但文案比较机械。当前先保证推荐准，再优化表达。

### 3. 交付包仍偏工程产物

状态：未完成。

已有 Markdown、JSON、CSV、manifest，但还没有 `.xlsx` 汇总和 `.zip` 交付包。当前优先级低于正确性和配置调优。

## 已完成，不再作为技术债

- 根包迁移到 `xft`。
- 删除 `src/diligence`。
- 删除根目录 `.py` 入口脚本。
- 删除旧兼容 wrapper。
- 删除 `xft pipeline` 通用入口。
- 删除根级旧尽调兼容转发模块。
- Docker 入口统一为 `xft`。
- 推荐主线稳定在 `xft.pipeline.recommender`。
- 尽调场景稳定在 `xft.pipeline.diligence`。
- `scoring_policy.yaml` 配置化。
- `evidence_policy.yaml` 配置化。
- Scenario bundle 继承和产品 patch。
- 推荐结果中的评分解释和证据解释。
- 业务标注校准 CLI 闭环。
- 搜索模型下沉到 `xft.core.search_models`。
- 配置审计 manifest。
- ruff 质量门禁恢复为绿色。
- 两条流水线冒烟验收流程已写入 `docs/SMOKE.md` 和 README。
- `tests/test_xft_cli.py` 已覆盖推荐离线冒烟入口和尽调 dry-run 冒烟入口。
- README 已从业务人员视角补充配置调优指南，覆盖产品规则、维度、评分、证据、Web、LLM、场景 patch 和校准验证。
- 业务版 `result.json` 与 `business_modules.yaml` 已落地。
- `sales_recommendation` 业务结果层已覆盖 7 个模块。
- Web 小批次已验证搜索、抓取、抽取、入库和缓存复用。

## 当前建议优先级

1. 扩大真实业务标注样本，校准 `business_modules.yaml`。
2. 人工抽查 Web 证据质量，收紧搜索词和抽取 prompt。
3. 验证第二个真实业务场景。
