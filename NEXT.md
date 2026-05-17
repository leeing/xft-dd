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

- 最近全量验证：`389 passed`。
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
- 业务标注校准 CLI 闭环，支持 `run_calibration.py --labels calibration_labels.csv`。
- 搜索模型下沉到 `xft.core.search_models`，`web/cache/utils` 已解除对旧尽调模型的历史依赖。

## 仍然存在的关键问题

### 1. Scenario 只能覆盖顶层文件，不能局部 patch 产品规则

现在 scenario 可以通过 `extends/overrides` 覆盖顶层路径，但如果一个新场景只想调整某个产品的 `base_score`、新增一条 rule，仍需要复制整份 `products.yaml`。

这会影响多场景产品化效率。当前单场景还可接受，第二个真实场景开始会变痛。

### 2. `scenario_resolved.json` 只记录路径，不记录内容 hash

当前 resolved config 方便看路径，但不能回答“这次报告到底用了哪版产品规则/维度/prompt/policy 内容”。

后续一旦做交付或回溯，需要把 resolved products、dimensions、web config、scoring policy、evidence policy、prompt hash 一并写入。

### 3. 真实 Web / LLM 校准样本仍偏少

已完成 1 个强制 Web 维度真实样本，验证了 MiniMax 搜索、crawl4ai 抓取、LLM 抽取、DuckDB 入库和报告链路。

当前仍缺业务标注后的 5-10 家样本，用来判断 Top1/可接受命中率是否满足业务预期。

## 下一步建议

我建议接下来按“产品规则 patch、配置审计、扩大带标注校准样本”的顺序走。

### Sprint H：业务标注校准闭环

状态：**已完成。**

已落地：

- `run_calibration.py --labels calibration_labels.csv`
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

任务：

1. 定义 patch schema。
2. 在 products loader 中按 `module_id` 应用 patch。
3. 处理规则追加、规则替换、规则删除。
4. `scenario_resolved.json` 写入 patch 后的 products hash。
5. 新增一个最小示例场景验证设计。

优先级：中高。等 Sprint H 后做更合适。

### Sprint K：真实 Web / LLM 小批次校准

状态：**已完成第一轮真实链路校准。**

目标：用真实抓取和 LLM 抽取验证 MVP V2 的业务质量。

已落地：

- `run_calibration.py` 支持 `--with-web --with-llm` 统一跑推荐校准。
- `run_calibration.py` 支持 `--force-web-dimensions`，可在本地画像充足时强制压测 Web 搜索链路。
- 校准报告新增 Web/LLM 模式、Web 证据覆盖率、搜索/抓取/抽取执行与复用指标。
- 每个校准批次新增 `web_llm_review_samples.csv`，便于业务人员人工抽查 Web 证据。
- `run_recommender.py` 同步支持 `--force-web-dimensions`。
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
uv run python run_calibration.py \
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

目标：让每次报告都可追溯到完整配置版本。

任务：

1. `scenario_resolved.json` 增加内容 hash：
   - products
   - dimensions
   - web_search
   - web_extract_llm
   - scoring_policy
   - evidence_policy
   - prompts
2. 每次推荐 run 目录写入 `config_manifest.json`。
3. batch delivery manifest 引用每个 run 的 config manifest。
4. README 说明如何复现一次交付。

优先级：中。

## 不建议现在做的事

### 1. 暂时不要先扩 Web provider

新增 Bing / Tavily / SerpAPI 需要写 adapter，有价值，但现在不是最高收益。当前 minimax / metaso / crawl4ai 的链路更需要先用真实批次验证噪声和命中率。

### 2. 暂时不要重写报告形态

Markdown 报告虽然不是最终商业版，但足够支撑校准。应先解决“推荐是否准、证据是否可信”，再做 PPT/Word/Excel 交付样式。

### 3. 暂时不要继续拆旧尽调 pipeline

旧尽调场景已经隔离在 `xft.pipeline.diligence`。除非它阻碍 recommender 主线，否则不应继续消耗当前迭代预算。

## 推荐执行顺序

1. Sprint J：Scenario 产品规则 patch。
2. Sprint L：交付审计增强。
3. 扩大 Sprint K：带业务标注的 5-10 家真实样本。

如果只能做一件事，就做 Sprint J。真实链路已经能跑，下一步最容易卡住多场景扩展的是产品规则需要整文件复制。
