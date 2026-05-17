# NEXT.md

本文档记录 2026-05-17 配置治理 Sprint 后，对当前代码仓库的重新审阅结论，以及下一步迭代建议。

## 当前状态判断

项目已经完成从“单一尽调脚本/推荐脚本”到 `xft` 企业分析底座的第一轮收敛。

当前真实结构是：

```text
src/xft/
  core/                    通用模型、维度分析、scenario bundle
  warehouse/               DuckDB 企业画像仓库
  evidence/                证据模型、证据仓库、冲突消解、evidence policy
  web/                     Web 搜索、抓取、抽取、缓存、入库
  scoring/                 配置驱动评分引擎、scoring policy
  ai/                      LLM client、JSON 抽取、置信度工具
  cache/                   SQL 两级缓存
  runtime/                 统一 pipeline、batch、质量报告、校准、交付
  pipeline/
    recommender/           销售产品推荐场景
    diligence/             旧尽调报告场景
```

`src/diligence` 旧目录已经删除。当前还保留的是 `xft.graph`、`xft.models`、`xft.nodes.*` 等根级兼容转发模块，用于承接旧尽调流水线内部模型和历史测试。

配置侧已经进入可用阶段：

- 业务推荐主入口应是 `config/scenarios/<scenario>/`。
- `scenario.yaml` 能统一声明产品、维度、Web、LLM、评分、证据策略、prompt、输出目录和 Web cache root。
- `scoring_policy.yaml` 已控制推荐通用分值。
- `evidence_policy.yaml` 已控制 Web 跳过阈值、证据质量分、冲突与推荐证据阈值。
- `products.yaml` 与 `analysis_dimensions.yaml` 继续承载业务模块、规则、维度、搜索词和维度 prompt。

测试与质量状态：

- 最近全量验证：`399 passed`。
- `ruff` / `mypy` 对 `src` 与入口脚本通过。
- 架构边界测试已覆盖 `xft.web` / `xft.scoring` 不反向依赖 recommender。

## 已完成事项

以下事项不应再作为下一步计划重复出现：

- 根包迁移到 `xft`。
- 删除 `src/diligence` 旧包名目录。
- 旧尽调流水线迁入 `xft.pipeline.diligence`。
- `xft.web` / `xft.scoring` 与 recommender 场景解耦。
- `xft.runtime.batch` 通用批量运行器。
- batch quality report / delivery manifest / failed companies 交付产物。
- scenario bundle 的 `extends` / `overrides` / `scenario_resolved.json`。
- `scoring_policy.yaml` 配置化。
- `evidence_policy.yaml` 配置化。
- 推荐结果 `result.json` 中的评分解释、证据解释、冲突摘要。
- Web cache 的 search/fetch/extraction 可复用与 cache report。
- 业务标注校准 CLI 闭环，支持 `uv run xft calibrate --labels calibration_labels.csv`。
- 搜索模型下沉到 `xft.core.search_models`，`web/cache/utils` 已解除对旧尽调模型的历史依赖。
- Scenario 产品规则 patch，支持按 `module_id` 局部调整产品规则。
- 配置审计 manifest，run/batch 产物可追溯配置 hash。
- `uv run xft <subcommand>` 统一 CLI，根目录 `.py` 脚本已清空。

## 仍然存在的关键问题

### 1. 真实 Web / LLM 校准样本仍偏少

已完成 1 个强制 Web 维度真实样本，验证了 MiniMax 搜索、crawl4ai 抓取、LLM 抽取、DuckDB 入库和报告链路。

当前仍缺业务标注后的 5-10 家样本，用来判断 Top1/可接受命中率是否满足业务预期。

### 2. 第二个真实业务场景还没有业务验证

`bank_marketing` 已作为产品 patch 示例落地，但还不是经过业务人员确认的真实场景。
后续需要用真实银行营销规则、真实标注样本和真实输出报告验证场景继承设计。

### 3. 交付形态仍偏工程产物

当前已有 Markdown、JSON、CSV、manifest，但还没有 `.xlsx` 汇总、`.zip` 交付包和面向业务人员的交付目录规范。

## 下一步建议

我建议接下来按“扩大带标注校准样本、真实业务场景验证、交付包增强”的顺序走。

### Sprint H：业务标注校准闭环

状态：**已完成。**

已落地：

- `uv run xft calibrate --labels calibration_labels.csv`
- `calibration_labels.example.csv`
- CSV 标注读取测试
- Top1 命中率 / 可接受命中率 / 错配案例测试
- README 业务标注校准说明

下一步应使用它跑真实小批次，而不是继续停留在工具层。

### Sprint I：搜索模型下沉

状态：**已完成。**

已落地：

- 新增 `xft.core.search_models`。
- `SearchItem`、`DimensionSearchResult`、`make_item_id` 已迁入通用 core。
- `xft.models` 和 `xft.pipeline.diligence.models` 保留兼容 re-export。
- `xft.web`、`xft.utils`、`xft.cache` 已直接 import `xft.core.search_models`。
- 边界测试禁止 `web/cache/utils` 依赖 `xft.pipeline.diligence.models`。

验收：

- `rg "from xft.models import|xft.pipeline.diligence.models" src/xft/web src/xft/utils src/xft/cache` 为空。

### Sprint J：Scenario 产品规则 patch

状态：**已完成。**

目标：让新场景只声明差异，而不是复制整份产品配置。

建议设计：

```yaml
extends: ../sales_recommendation
id: bank_marketing
name: 银行业营销场景

patches:
  products:
    - module_id: crm_channel
      set:
        base_score: 55
      append_positive_rules:
        - id: bank_high_quality_customer
          source_field: bank_flags.high_quality_customer
          op: "=="
          value: true
          weight: 12
          reason: 银行高质量客户标签提示金融服务匹配度更高
```

已落地：

- `ScenarioConfig.patches` 支持声明产品 patch。
- `load_products_config()` 会按 `module_id` 应用 patch。
- 支持 `set`、规则追加、规则替换、规则删除。
- 推荐 run 目录写入 `scenario_resolved.json`，包含 patch 后的 `products_effective_hash`。
- 新增 `config/scenarios/bank_marketing/scenario.yaml` 作为最小继承/patch 示例。

后续只需要在新增真实场景时扩展 patch 内容，不再复制整份产品配置。

### Sprint K：真实 Web / LLM 小批次校准

状态：**已完成第一轮真实链路校准。**

目标：用真实抓取和 LLM 抽取验证 MVP V2 的业务质量。

已落地：

- `uv run xft calibrate` 支持 `--with-web --with-llm` 统一跑推荐校准。
- `uv run xft calibrate` 支持 `--force-web-dimensions`，可在本地画像充足时强制压测 Web 搜索链路。
- 校准报告新增 Web/LLM 模式、Web 证据覆盖率、搜索/抓取/抽取执行与复用指标。
- 每个校准批次新增 `web_llm_review_samples.csv`，便于业务人员人工抽查 Web 证据。
- `uv run xft recommend` 同步支持 `--force-web-dimensions`。
- 已跑真实样本：
  - `recommendation_runs/calibration/web-calibration-20260517`
  - `recommendation_runs/calibration/web-calibration-force-20260517`

真实校准观察：

- 默认策略下，画像完整企业会跳过 Web 搜索，符合“本地 JSON 已充足则不重复搜索”的原则。
- 强制 Web 样本触发 30 次搜索、150 条搜索结果、10 次 LLM 抽取，最终 3 条 Web 证据入库。
- crawl4ai 暴露了真实网页质量问题：反爬、PDF/Office 文件、短页面、无正文页面较多。
- LLM 相关性过滤生效，多数非目标企业或低相关结果被过滤。

后续建议：

1. 选 5-10 家业务人员熟悉的企业。
2. 补 `calibration_labels.csv` 后跑：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10 \
  --with-web \
  --with-llm \
  --batch-id web-label-calibration-01
```

3. 人工检查：
   - Web 搜索是否跳过了本地已充足维度。
   - Web 证据是否与目标公司相关。
   - 冲突是否以 JSON 为准。
   - report.md 是否解释清楚。
4. 根据报告调 `products.yaml`、`scoring_policy.yaml`、`evidence_policy.yaml` 和 prompt。

优先级：中。第一轮真实链路已验证，下一步更需要带标注样本扩大规模。

### Sprint L：交付审计增强

状态：**已完成。**

目标：让每次报告都可追溯到完整配置版本。

已落地：

- 每次 recommender run 写入 `config_manifest.json`。
- manifest 记录 products、dimensions、web_search、web_extract_llm、scoring_policy、evidence_policy 和 prompts 的路径、存在状态、sha256 与字节数。
- manifest 记录 patch 后的 products、dimensions、scoring_policy、evidence_policy effective hash。
- batch `delivery_manifest.json` 自动引用每个 run 的 `config_manifest.json` 和 `scenario_resolved.json`。
- README 已补充复现入口说明。

下一步若继续做交付工程化，可以把 manifest 扩展到旧 diligence pipeline 和 Web enrichment 独立运行产物。

## 不建议现在做的事

### 1. 暂时不要先扩 Web provider

新增 Bing / Tavily / SerpAPI 需要写 adapter，有价值，但现在不是最高收益。当前 minimax / metaso / crawl4ai 的链路更需要先用真实批次验证噪声和命中率。

### 2. 暂时不要重写报告形态

Markdown 报告虽然不是最终商业版，但足够支撑校准。应先解决“推荐是否准、证据是否可信”，再做 PPT/Word/Excel 交付样式。

### 3. 暂时不要继续拆旧尽调 pipeline

旧尽调场景已经隔离在 `xft.pipeline.diligence`。除非它阻碍 recommender 主线，否则不应继续消耗当前迭代预算。

## 推荐执行顺序

1. 扩大 Sprint K：带业务标注的 5-10 家真实样本。
2. 增加第二个真实业务场景并使用产品 patch 落地。
3. 交付包增强：`.xlsx` 汇总和 `.zip` 交付包。

如果只能做一件事，就做带标注真实样本。CLI 和配置入口已经收敛，下一步需要业务质量反馈。
