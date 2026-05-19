# NEXT.md

本文档记录当前优先级和下一步迭代顺序。当前不追求更多抽象，优先保证正确性、可配置性和文档一致性。

## 当前优先级

### 1. 保证两个流水线正确运行

当前必须稳定的两条流水线：

```text
uv run xft recommend   # 产品推荐流水线，当前主线
uv run xft diligence   # 企业尽调流水线，保留场景
```

本轮已验证：

- `uv run xft --help`：通过
- `uv run xft scenario validate config/scenarios/sales_recommendation`：通过
- `uv run xft scenario validate config/scenarios/bank_marketing`：通过
- `uv run xft recommend --no-llm --scenario config/scenarios/sales_recommendation "安徽扬山联合精密技术有限公司"`：通过，生成 `report.md`、业务版 `result.json`、`internal_result.json` 和 `business_label_result.json`
- `uv run xft diligence --dry-run "安徽扬山联合精密技术有限公司"`：通过，不触发外部调用
- CLI / 冒烟入口测试：`18 passed`
- 关键测试：`18 passed`
- 全量测试：`457 passed`
- `uv run mypy src`：通过
- `uv run ruff check src tests`：通过

当前基础质量门禁已经恢复为绿色。

### 2. 业务人员能通过配置验证和调优

配置主入口是：

```text
config/scenarios/sales_recommendation/
```

业务人员优先修改：

| 想调什么 | 文件 |
|----------|------|
| 产品模块、权重、命中规则 | `products.yaml` |
| 业务版 `result.json`、标签、指标、营销点、KYC 问题 | `business_modules.yaml` |
| 分析维度、本地字段、Web 搜索词 | `analysis_dimensions.yaml` |
| 全局评分口径 | `scoring_policy.yaml` |
| 证据优先级、Web 跳过、冲突处理 | `evidence_policy.yaml` |
| Web provider、抓取和缓存策略 | `web_search.yaml` |
| Web 抽取模型 | `web_extract_llm.yaml` |
| LLM 文案和抽取要求 | `prompts/*.md` |
| 场景入口和继承/patch | `scenario.yaml` |

配置验证命令：

```bash
uv run xft scenario validate config/scenarios/sales_recommendation
uv run xft scenario inspect config/scenarios/sales_recommendation
```

调优验证命令：

```bash
uv run xft recommend --no-llm --scenario config/scenarios/sales_recommendation "企业名称"
uv run xft calibrate --scenario config/scenarios/sales_recommendation --company-list company.txt --limit 10
```

### 3. README 和 docs 反映最新情况

根目录 README 面向业务人员，docs 面向开发者和后续维护。

当前文档职责：

| 文档 | 职责 |
|------|------|
| `README.md` | 业务人员如何跑、如何配置、如何看结果 |
| `docs/ARCHITECTURE.md` | 当前真实架构、两条流水线、数据流和配置体系 |
| `docs/NEXT.md` | 下一步优先级 |
| `docs/TECH_DEBT.md` | 当前真实技术债 |

本轮已将 `docs/ARCHITECTURE.md` 改为当前架构，不再沿用旧的通用 runtime pipeline 描述。

## 当前真实结构

```text
src/xft/
  cli/                      xft 命令入口
  pipeline/recommender/     产品推荐流水线
  pipeline/diligence/       企业尽调流水线
  core/                     通用模型、场景配置、维度分析、搜索模型
  warehouse/                DuckDB 企业画像仓库、Prophet JSON adapter
  evidence/                 证据模型、证据仓库、冲突消解
  web/                      Web 搜索、抓取、抽取、缓存、入库
  scoring/                  配置驱动评分引擎
  ai/                       LLM client 和 JSON 抽取
  cache/                    SQL 缓存层
  runtime/                  artifacts、calibration、config_manifest
```

当前没有：

- 根目录 `.py` 脚本入口
- `scripts/compat/` 兼容 wrapper
- `xft pipeline` 通用命令
- 通用 runtime runner/batch/models
- 根级旧尽调兼容转发模块

## 下一步建议

### Sprint M：流水线正确性收尾

状态：**已完成。**

目标：建立两条流水线的最小稳定验收集。

已落地：

- 推荐流水线验收命令：

```bash
uv run xft recommend --no-llm --scenario config/scenarios/sales_recommendation "企业名称"
```

- 尽调流水线验收命令：

```bash
uv run xft diligence --dry-run "企业名称"
```

- `docs/SMOKE.md` 记录完整冒烟验收流程。
- README 已链接冒烟验收流程。
- `tests/test_xft_cli.py` 已覆盖推荐离线冒烟入口和尽调 dry-run 冒烟入口。
- 以下命令已同时通过：

```bash
uv run pytest -q
uv run mypy src
uv run ruff check src tests
```

如果后续要验证真实外部调用，应单独跑带 Web/LLM 的小样本，不和日常基础验收混在一起。

### Sprint N：配置调优闭环

状态：**基础能力已完成，等待业务标注样本校准。**

目标：让业务人员不改代码也能验证和调优推荐效果。

已落地：

- README 已从业务人员视角补充配置调优指南。
- 调优指南覆盖产品规则、分析维度、评分策略、证据策略、Web 搜索、LLM prompt、场景 patch 和校准验证。
- 新增 `business_modules.yaml`，业务人员可配置模块、标签、指标、判断器、营销点和 KYC 问题。
- `result.json` 已切换为业务交付格式，内部评分保留在 `internal_result.json`。
- 当前销售推荐场景已覆盖 7 个业务模块：假勤管理、差旅报销、对公报账、个税管理、日常报销、进项发票、销项发票。

剩余任务：

1. 准备 5-10 家带业务标注的样本。
2. 跑：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10
```

3. 根据错配案例调：
   - `products.yaml`
   - `analysis_dimensions.yaml`
   - `scoring_policy.yaml`
   - `evidence_policy.yaml`
   - prompts

优先级：高。

### Sprint O：文档一致性维护

目标：让 README 和 docs 始终跟代码一致。

任务：

1. README 只保留业务人员关心的运行与配置说明。
2. `docs/ARCHITECTURE.md` 只描述当前真实架构，不保留旧方案长篇历史。
3. `docs/TECH_DEBT.md` 只保留真实还存在的问题。
4. 每次入口、配置、目录结构变化后同步更新这三份文档。

优先级：高。

### Sprint P：真实 Web / LLM 小批次验证

状态：**Web 小批次链路已验证，仍需业务质量校准。**

目标：在基础流水线稳定后，再验证 Web 和 LLM 对推荐质量的提升。

已验证：

- 本地证据足够时，`--with-web` 能正确跳过搜索。
- `--force-web-dimensions` 能触发真实 Web 搜索、抓取、抽取和入库。
- 二次运行同一企业时，能复用已有 DuckDB Web 证据。
- Web 证据能进入 `dimension_analysis.json` 和推荐链路。

任务：

```bash
uv run xft calibrate \
  --scenario config/scenarios/sales_recommendation \
  --company-list company.txt \
  --labels calibration_labels.csv \
  --limit 10 \
  --with-web \
  --with-llm
```

观察：

- Web 是否只在本地证据不足时触发。
- Web 证据是否与目标企业相关。
- LLM 抽取是否稳定。
- 冲突是否以本地 JSON 为准。
- 推荐命中率是否提升。

优先级：中。不要早于基础正确性和配置闭环。

## 暂不优先

当前暂不优先做：

- 新增更多 Web provider。
- 恢复 `xft pipeline`。
- 恢复脚本兼容入口。
- 大规模重写报告样式。
- 过早做 `.xlsx` / `.zip` 交付包。

这些都可以后置。当前先保证正确性、可配置性、文档一致性。
