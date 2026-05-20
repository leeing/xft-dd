# TECH_DEBT.md

本文档记录当前真实存在的技术债。旧推荐链、旧维度分析、旧 Web enrichment 已删除，当前优先级是推荐准确性、配置可维护性和业务验收。

## 当前基线

产品化 CLI：

- `uv run xft recommend`：产品推荐流水线
- `uv run xft calibrate`：推荐校准
- `uv run xft warehouse build`：本地 JSON 入库
- `uv run xft scenario validate/inspect`：场景配置校验和审计
- `uv run xft runs inspect`：运行产物汇总
- `uv run xft cache ...`：远端/本地缓存管理

推荐主线：

```text
data_gather -> web_evidence -> recommend -> save
```

## 高优先级技术债

### 1. 真实业务标注样本不足

状态：未完成。

现状：

- 校准 CLI 已可用。
- `sales_recommendation` 已覆盖 7 个模块。
- `rule / llm / hybrid / llm_web` 已支持。
- `假勤管理`、`差旅报销` 已完成样板配置治理，后续应按同样方式治理 `日常报销`、`销项发票` 等模块。
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

根据错配案例调整 `modules.d/*.yaml`。

### 2. 业务 Web 证据仍需抽样确认

状态：未完成。

现状：

- 配置了 `web_search` 的指标可以通过 `--with-web` 执行固定查询；`auto.enabled` 可生成少量补充查询。
- 业务 Web 证据会进入 `indicator_evidence.json`。
- 需要确认查询词、provider 结果和证据噪声是否满足销售使用。

建议：

- 检查 `data_sources.type=table` + `op=text_contains` 的指标是否配置了具体 `keywords`，不能留空。
- 将已有本地结构化证据的 `llm_web` 指标改为 `rule` 或 `hybrid`。
- 选 2-3 家企业人工检查 `web_trace.json`。
- 检查 `indicator_evidence.json` 中 `source_type=web` 的证据是否可用于判断。
- 根据噪声调整指标级 `web_search.when/effect/fixed_queries/auto`。

### 3. 新业务场景尚未沉淀

状态：未完成。

现状：

- 当前正式推荐场景是 `sales_recommendation`。
- 其他业务场景需要独立配置和标注验收。

建议：

- 复制 `sales_recommendation` 建立新场景。
- 独立维护新场景 `modules.d/*.yaml`。
- 用相同企业对比两个场景的 `result.json`。

## 中低优先级技术债

### 1. 批量质量报告仍可增强

当前批量摘要已适配业务推荐结果，但证据覆盖率、LLM fallback、无推荐企业、低分企业可以做成更直观的业务质量报告。

### 2. 报告文案仍偏模板

无 LLM 模式能稳定跑通，但 `report.md` 的表达仍偏结构化模板。当前先保证推荐准，再优化表达。

### 3. 交付包仍偏工程产物

已有 Markdown、JSON、CSV、manifest，但还没有 `.xlsx` 汇总和 `.zip` 交付包。

## 已完成，不再作为技术债

- 根包迁移到 `xft`。
- 删除旧企业调研流水线代码、配置和入口，项目聚焦推荐单主线。
- 删除根目录 `.py` 入口脚本。
- 删除旧兼容 wrapper。
- Docker 入口统一为 `xft`。
- 推荐主线稳定在 `xft.pipeline.recommender`。
- 业务标注校准 CLI 闭环。
- 搜索模型下沉到 `xft.core.search_models`。
- 配置审计 manifest。
- ruff / mypy / pytest 质量门禁恢复为绿色。
- README 改为业务人员视角。
- 业务版 `result.json` 与 `modules.yaml` 落地。
- `sales_recommendation` 业务结果层覆盖 7 个模块。
- 模块配置拆分为 `modules.d/*.yaml`。
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
- 删除旧维度与旧 Web enrichment：
  - `analysis_dimensions.yaml`
  - `evidence_policy.yaml`
  - `web_extract_llm.yaml`
  - `dimension_analyze_node.py`
  - `web_evidence_node.py`
  - `xft web`
  - `warehouse web-import`

## 当前建议优先级

1. 扩大真实业务标注样本，校准 `modules.d/*.yaml`。
2. 人工抽查业务 Web 证据质量。
3. 验证第二个真实业务场景。
