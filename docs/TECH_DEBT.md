# TECH_DEBT.md

本文档记录当前真实存在的技术债。旧推荐链已经删除，当前优先级是正确性、可配置性和文档一致性。

## 当前基线

产品化 CLI：

- `uv run xft recommend`：产品推荐流水线
- `uv run xft diligence`：企业尽调流水线
- `uv run xft calibrate`：推荐校准
- `uv run xft web enrich/import`：Web 补证和入库
- `uv run xft warehouse build`：本地 JSON 入库
- `uv run xft scenario validate/inspect`：场景配置校验和审计

推荐主线已经收敛到：

```text
data_gather -> dimension_analyze -> web_evidence -> business_recommend -> save
```

## 高优先级技术债

### 1. 真实业务标注样本不足

状态：未完成。

现状：

- 校准 CLI 已可用。
- `business_modules.yaml` 已覆盖 7 个模块。
- `rule / llm / hybrid` 已支持。
- 缺少业务人员标注后的 5-10 家真实样本。

影响：

- 当前能证明流水线可运行，不能证明推荐足够准。

建议：

```bash
uv run xft calibrate \
  --scenario config/recommend/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

根据错配案例调整 `business_modules.yaml`、`analysis_dimensions.yaml`、`evidence_policy.yaml` 和 Web/LLM prompt。

### 2. Web 结果质量仍需业务侧抽样确认

状态：未完成。

现状：

- Web 搜索、抓取、抽取、入库和缓存复用已经跑通。
- Web 证据进入 `dimension_analysis.json`。
- 但需要确认噪声、重复和无关网页是否会影响业务指标。

建议：

- 选 2-3 家企业人工检查 `dimension_analysis.json` 中的 `web_evidence`。
- 根据噪声来源调整搜索词、blocked domains 和 Web 抽取 prompt。

### 3. `bank_marketing` 仍是示例场景

状态：未完成。

现状：

- `bank_marketing` 已能继承 `sales_recommendation`。
- 但尚未承载真实银行营销规则。

建议：

- 建立独立的银行营销 `business_modules.yaml`。
- 用同一批企业对比两个场景的 `result.json`。

## 中低优先级技术债

### 1. 批量质量报告仍可增强

当前批量摘要已适配业务推荐结果，但 Web cache 命中、LLM fallback、冲突数量、证据覆盖率还可以做成更直观的质量报告。

### 2. 报告文案仍偏模板

无 LLM 模式能稳定跑通，但 `report.md` 的表达仍偏结构化模板。当前先保证推荐准，再优化表达。

### 3. 交付包仍偏工程产物

已有 Markdown、JSON、CSV、manifest，但还没有 `.xlsx` 汇总和 `.zip` 交付包。

## 已完成，不再作为技术债

- 根包迁移到 `xft`。
- 删除 `src/diligence`。
- 删除根目录 `.py` 入口脚本。
- 删除旧兼容 wrapper。
- Docker 入口统一为 `xft`。
- 推荐主线稳定在 `xft.pipeline.recommender`。
- 尽调场景稳定在 `xft.pipeline.diligence`。
- `evidence_policy.yaml` 配置化。
- 业务标注校准 CLI 闭环。
- 搜索模型下沉到 `xft.core.search_models`。
- 配置审计 manifest。
- ruff / mypy 质量门禁恢复为绿色。
- README 已改为业务人员视角。
- 业务版 `result.json` 与 `business_modules.yaml` 已落地。
- `sales_recommendation` 业务结果层已覆盖 7 个模块。
- Web 小批次已验证搜索、抓取、抽取、入库和缓存复用。
- 删除旧推荐链：
  - `llm_match_node.py`
  - `llm_recommend_node.py`
  - `recommendation_normalizer.py`
  - `src/xft/scoring/`
  - `products.yaml`
  - `scoring_policy.yaml`
  - `config/recommender/`
  - `internal_result.json`
  - `match_results.json`
- 删除旧版非场景化 `data/web/<公司>/` 缓存目录。
- 配置目录按业务入口拆分为 `config/recommend/` 和 `config/diligence/`。

## 当前建议优先级

1. 扩大真实业务标注样本，校准 `business_modules.yaml`。
2. 人工抽查 Web 证据质量。
3. 验证第二个真实业务场景。
