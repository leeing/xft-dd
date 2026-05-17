# TECH_DEBT.md

本文档记录 Sprint G 完成后的技术债务回顾，目的是给后续继续平台化、业务试跑和交付工程化留一张清晰的地图。

## 当前状态

项目已经完成从单一尽调/推荐脚本到 `xft` 企业分析底座的第一轮架构收敛：

- `xft.core`：通用模型、scenario bundle、维度分析、配置读取。
- `xft.warehouse`：DuckDB 企业画像仓库。
- `xft.evidence`：证据模型、证据仓库、冲突消解。
- `xft.web`：Web 搜索、抓取、抽取、缓存、入库。
- `xft.scoring`：配置驱动评分引擎。
- `xft.runtime`：统一 pipeline request/result、质量报告、交付清单、校准报告。
- `xft.pipeline.recommender`：销售产品推荐场景。
- `xft.pipeline.diligence`：旧尽调流水线场景化。

Sprint G 新增了推荐校准工具：

```bash
uv run python run_calibration.py --limit 10 --batch-id sprint-g-final-10
```

本地试跑结果：

- 批次：`recommendation_runs/calibration/sprint-g-final-10`
- 企业数：10
- 成功：10
- 平均 Top 推荐分：84.0
- Top1 分布：`hr_attendance` 5 次、`crm_channel` 5 次
- 未发现低画像完整度、无推荐、高冲突企业

## 已修复的关键问题

### 1. 推荐分数饱和

试跑前，10 家企业全部 Top1 为 `procurement_srm`，且多个产品都达到 100 分。

原因：

- `dimension_support` 和 `evidence_support` 加分过高。
- 高完整度企业的 10 个维度几乎全部 supported。
- 分数饱和后排序退化为产品配置顺序。

已处理：

- 降低通用维度支持和本地证据支持的加分权重。
- 保留产品规则作为主要区分因素。
- 同分排序改为优先负向扣分少、正向规则更强、维度支持更强的产品。

### 2. 缺少可重复校准工具

已新增：

- `xft.runtime.calibration`
- `run_calibration.py`
- `tests/test_runtime_calibration.py`

后续规则调整可以通过固定批次反复验证，而不是人工逐个翻报告。

## 高优先级技术债务

### 1. `diligence` 兼容路径仍是复制体

当前 `src/diligence/*` 仍然存在不少旧代码副本，而不是完全转发到 `xft.*`。

风险：

- 修复 `xft` 后旧路径可能行为不一致。
- 测试仍大量引用 `diligence.*`，容易掩盖主线迁移问题。

建议：

1. 把 `src/diligence/*` 全部改为薄转发层。
2. 测试主线 import 改为 `xft.*`。
3. 只保留少量 alias 兼容测试覆盖 `diligence.*`。

### 2. Batch runner 尚未完全平台化

当前已经平台化了 artifact/quality/delivery，但批量执行仍在：

- `xft.pipeline.recommender.batch`
- `xft.pipeline.diligence.batch`

建议：

- 新增 `xft.runtime.batch`。
- 让 batch runner 基于 `PipelineRunRequest` / `PipelineRunResult` 执行任意 pipeline。
- 场景只提供 batch row summarizer。

### 3. `SearchItem` 仍挂在旧 diligence 模型上

`xft.web`、`xft.utils`、`xft.cache` 仍通过 `xft.models.SearchItem` 使用旧搜索模型。

建议：

- 新增 `xft.core.search_models`。
- 迁移 `SearchItem / DimensionSearchResult / make_item_id`。
- 根级 `xft.models` 继续兼容转发。

### 4. Scenario YAML 只能顶层覆盖

Sprint F 已支持 scenario 顶层 `extends/overrides`，但无法对 `products.yaml` 内单个产品规则做结构化 patch。

建议：

- 新增 config patch 机制，例如按 `module_id` 合并 product rules。
- 输出 `scenario_resolved.json` 时附带 resolved products/dimensions/web config hash。

### 5. 校准还缺少业务标注闭环

当前校准只能看分布、分数、冲突和完整度，缺少“业务人员判断 Top1 是否正确”的反馈入口。

建议：

- 新增 `calibration_labels.csv`：
  - company_name
  - expected_top_module
  - acceptable_modules
  - comment
- 校准报告计算 Top1 命中率、可接受命中率和错配案例。

## 中优先级技术债务

### 1. Web/LLM 运行指标还没有统一采集

质量报告还没有标准化展示：

- search 执行/复用次数
- fetch 执行/复用次数
- extraction 执行/复用次数
- LLM fallback 比例
- provider 错误分布

建议把 `web_cache_report.md/json` 的核心指标接入 `xft.runtime.artifacts`。

### 2. 评分参数仍写在代码里

Sprint G 将评分常量抽成代码常量，但还没有配置化。

建议：

- 新增 `scoring_policy.yaml`。
- 将 dimension/evidence/web/conflict/missing 权重迁到配置。
- scenario 可 override scoring policy。

### 3. 报告文案仍依赖 fallback 模板

无 LLM 模式可跑通，但报告表达比较机械。

建议：

- 把 fallback 推荐文案模板配置化。
- 每个产品模块增加 `pitch_template`。
- 报告中明确标识“规则模式/LLM 模式/Web 模式”。

### 4. 缺少真实 Web 校准批次

Sprint G 只跑了本地画像模式，没有开启 Web/LLM。

建议：

1. 选 5 家企业跑 `--with-web`。
2. 检查 Web 证据噪声、冲突和缓存复用。
3. 再跑 `--with-llm` 验证最终报告表达质量。

## 建议下一步

我建议优先做三件事：

1. **兼容层瘦身**：把 `diligence.*` 全部变成 `xft.*` 转发，减少双代码路径。
2. **统一 batch runner**：新增 `xft.runtime.batch`，让 recommender/diligence 批量运行共用一套执行协议。
3. **业务标注校准**：给 Sprint G 的校准工具加 `calibration_labels.csv`，把“业务直觉”纳入可测试指标。

