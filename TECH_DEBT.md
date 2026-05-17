# TECH_DEBT.md

本文档记录 2026-05-17 配置治理 Sprint 后仍然真实存在的技术债务。已完成的历史迁移和配置化事项不再保留为待办。

## 当前基线

项目已经完成第一轮平台化收敛：

- 根包统一为 `src/xft`，`src/diligence` 旧目录已删除。
- `xft.pipeline.recommender` 是当前主力销售产品推荐场景。
- `xft.pipeline.diligence` 是旧尽调报告场景，已隔离为 pipeline。
- `xft.warehouse`、`xft.evidence`、`xft.web`、`xft.scoring`、`xft.ai`、`xft.cache`、`xft.runtime` 已形成通用基础设施。
- Scenario bundle 已支持产品、维度、Web、LLM、评分、证据策略和 prompt 的统一配置入口。
- `scoring_policy.yaml` 与 `evidence_policy.yaml` 已接入运行链路。
- `xft.runtime.batch`、质量报告、交付清单、失败清单已经可用。
- 最近验证基线：`ruff` / `mypy` 通过，`pytest` 为 `389 passed`。

## 高优先级技术债务

### 1. Scenario 只能顶层覆盖，不能局部 patch 产品规则

状态：未完成。

现状：

- `scenario.yaml` 支持 `extends` / `overrides`。
- 但 overrides 主要覆盖顶层路径或简单字段。
- 如果只想调整某个产品的一条规则，需要复制整份 `products.yaml`。

风险：

- 第二个、第三个业务场景会产生大量重复配置。
- 产品规则修复需要同步多份 YAML，容易漂移。

建议：

- 增加按 `module_id` 合并的 product patch 机制。
- 支持 `set`、`append_positive_rules`、`append_negative_rules`、`append_exclusion_rules`、`remove_rules`。
- `scenario_resolved.json` 输出 patch 后的产品配置 hash。

## 中优先级技术债务

### 1. Scenario 审计信息还不够完整

状态：未完成。

现状：

- `scenario_resolved.json` 能写出解析后的路径。
- 但没有写入配置内容 hash。
- 单次推荐 run 目录没有独立 `config_manifest.json`。

风险：

- 交付报告后难以复现“当时到底用的是哪版配置”。
- 配置改动频繁时，排查推荐差异会变慢。

建议：

1. 对 products、dimensions、web_search、web_extract_llm、scoring_policy、evidence_policy、prompts 计算 hash。
2. run 目录写 `config_manifest.json`。
3. batch delivery manifest 引用每个 run 的 config manifest。

### 2. 报告文案仍依赖 fallback 模板

状态：未完成。

现状：

- 无 LLM 模式可以稳定跑通。
- 但 fallback 推荐文案比较机械。

风险：

- 对业务人员来说，报告可读性和销售可用性还不够。

建议：

- 每个产品模块增加 `pitch_template`。
- 报告明确标识规则模式、LLM 模式、Web 模式。
- 在真实校准后再优化报告，不要早于推荐质量验证。

### 3. 真实 Web / LLM 带标注样本仍偏少

状态：未完成。

现状：

- 第一轮真实链路校准已完成。
- 已验证默认跳过策略、强制 Web 搜索、crawl4ai 抓取、LLM 抽取过滤、DuckDB 入库和报告读取。
- 已生成 `web_llm_review_samples.csv`，但还缺业务人员标注后的 5-10 家样本。

风险：

- 当前只能说明链路可跑，不能说明推荐 Top1/可接受命中率已经达到业务标准。

建议：

1. 业务人员补 `calibration_labels.csv`。
2. 跑 5-10 家 `--with-web --with-llm --labels`。
3. 根据错配案例调整产品规则、评分策略、证据策略和 prompt。

### 4. 根级旧尽调兼容模块仍存在

状态：可接受，暂不优先。

现状：

- `xft.graph`、`xft.config`、`xft.models`、`xft.nodes.*` 等仍是旧尽调流水线兼容转发。
- `src/diligence` 已删除，因此这不是双包路径问题。

风险：

- 新开发者可能误用根级兼容入口，而不是 `xft.pipeline.diligence.*`。

建议：

- README 明确新代码不要使用这些根级兼容入口。
- 等搜索模型下沉后，再评估是否逐步删除 `xft.models` 的旧兼容含义。

## 当前建议优先级

1. **Scenario 产品规则 patch**：多场景前必须做，否则 YAML 复制会快速失控。
2. **配置审计 manifest**：交付工程化前必须做。
3. **真实 Web / LLM 带标注样本扩大**：链路已验证，下一步要验证业务命中率。

## 不再作为技术债跟踪的事项

以下事项已经完成，不再列为 debt：

- 根包迁移到 `xft`。
- 删除 `src/diligence`。
- `xft.web` / `xft.scoring` 解耦 recommender。
- 旧尽调流水线迁入 `xft.pipeline.diligence`。
- 通用 runtime batch。
- batch quality report / delivery manifest。
- `scoring_policy.yaml` 配置化。
- `evidence_policy.yaml` 配置化。
- 推荐结果中的评分解释和证据解释。
- 业务标注校准 CLI 闭环。
- 搜索模型下沉到 `xft.core.search_models`。
- 真实 Web / LLM 第一轮小批次校准。
