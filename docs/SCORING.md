# 推荐原理：规则引擎与 LLM 的分工

本文档面向业务人员，解释推荐流水线如何做出判断、分数怎么来的、LLM 承担什么角色。不涉及代码细节。

## 两层架构

推荐流水线由两个独立的引擎协作完成：

| 引擎 | 职责 | 特点 |
|------|------|------|
| **规则评分引擎** | 计算每个产品的推荐分数 | 确定性、可追溯、可审计 |
| **大语言模型（LLM）** | 润色文案、生成话术 | 辅助性、可降级、不影响分数 |

**规则引擎是决策核心，LLM 是表达层。** 关闭 LLM（`--no-llm`）后，推荐结果和分数完全不变，只是文案会从模板生成而非 AI 润色。

## 数据基础：企业画像有哪些字段

所有推荐逻辑（维度分析、规则评估、评分）都基于企业画像中的字段。理解有哪些字段、每个字段代表什么含义，是写好配置的前提。

### 数据从哪来

```
Prophet/NewEnt 系统（企业数据平台）
  → data/ 目录（每家企业的 JSON 数据包）
  → xft warehouse build（导入本地数据库）
  → company_profile 表（每家一行，约 40 个字段）
```

运行 `xft warehouse build` 后，企业的数据就在本地了。后续所有推荐命令都从这个本地数据库读取，不需要再联网。

### 字段速查表

以下是在配置文件中可以直接引用的字段，按业务含义分组：

#### 企业基础信息

| 字段名 | 含义 |
|--------|------|
| `company_name` | 企业名称 |
| `industry` | 行业一级分类 |
| `industry_big` | 行业二级分类 |
| `industry_small` | 行业三级分类 |
| `employee_count` | 员工人数 |
| `company_org_type` | 企业类型（有限责任公司、股份有限公司、个体工商户等） |
| `registered_capital` | 注册资本 |
| `registered_location` | 注册地 |
| `province` | 所在省份 |
| `business_scope` | 经营范围 |
| `established_at` | 成立日期 |
| `legal_person` | 法定代表人 |
| `reg_status` | 登记状态（存续、在业、注销等） |
| `is_listed` | 是否上市公司（是/否） |
| `stock_code` | 股票代码 |
| `website` | 企业官网 |
| `labels` | 企业标签列表，如"高新技术企业""专精特新"等 |

#### 规模与组织

| 字段名 | 含义 |
|--------|------|
| `branch_count` | 分支机构数量 |
| `recruitment_count` | 近期招聘信息条数 |
| `recent_recruitment_titles` | 近期招聘岗位名称列表 |

#### 知识产权

| 字段名 | 含义 |
|--------|------|
| `ip_counts` | 知识产权数量（嵌套字段，见下方说明） |
| `ip_counts.trademark` | 商标数量 |
| `ip_counts.patent` | 专利数量 |
| `ip_counts.software` | 软件著作权数量 |
| `ip_counts.works_copyright` | 作品著作权数量 |
| `ip_counts.website_filing` | 网站备案数量 |

#### 风险

| 字段名 | 含义 |
|--------|------|
| `risk_counts` | 风险计数（嵌套字段） |
| `risk_counts.self` | 自身风险数量 |
| `risk_counts.pre` | 预警风险数量 |
| `risk_counts.around` | 周边风险数量 |
| `risk_counts.court_session` | 开庭公告数量 |
| `risk_counts.judgement_doc` | 裁判文书数量 |
| `risk_counts.announcement` | 法院公告数量 |
| `risk_counts.inspection` | 抽查检查数量 |
| `risk_counts.change` | 工商变更数量 |

#### 招投标与资质

| 字段名 | 含义 |
|--------|------|
| `bidding_total` | 招投标总数 |
| `qualification_count` | 资质/证书数量 |

#### 股东与资本

| 字段名 | 含义 |
|--------|------|
| `shareholder_summary` | 前 8 大股东摘要（名称、出资金额、持股比例） |
| `registered_capital` | 注册资本 |
| `financing_event_count` | 历史融资次数 |
| `outbound_investment_count` | 对外投资企业数量 |

#### 银行与跨境标签

| 字段名 | 含义 |
|--------|------|
| `bank_flags` | 银行标签（嵌套字段） |
| `bank_flags.high_quality_customer` | 是否为银行高质量客户（是/否） |
| `bank_flags.credit_granting_customer` | 是否为授信客户（是/否） |
| `cross_border_flags` | 跨境业务标签（嵌套字段） |
| `cross_border_flags.labels` | 跨境标签列表，如"小额出口""服务贸易"等 |

#### 数据质量

| 字段名 | 含义 |
|--------|------|
| `profile_completeness` | 画像完整度（0-1），1 表示数据最全 |
| `missing_v1_files` | 缺失的标准数据文件列表（空 = 数据齐全） |

### 嵌套字段的引用语法

部分字段的值不是简单的数字或文字，而是包含多层信息的复合字段（技术术语叫 JSON）。配置文件中用 **点号** 逐层访问：

```
ip_counts.patent         → 从知识产权计数字段中取出专利数量
risk_counts.self         → 从风险计数字段中取出自身风险数量
bank_flags.high_quality_customer → 从银行标签中取出是否高质量客户
cross_border_flags.labels → 从跨境标签中取出标签列表
```

写配置时直接使用上面"字段速查表"中列出的名称即可，不需要关心底层的 JSON 结构。

### 字段怎么被配置使用

字段在三类配置中被引用，作用各不相同：

**① 证据模板 — 决定"这个维度有没有数据支撑"**

```yaml
evidence_templates:
  - field: employee_count      # 引用字段名
    label: 员工规模             # 报告里显示的中文名
```

如果企业有员工数据（不为空、不为 0），系统就生成一条事实"员工规模：850 人"。一个维度下每有一条这样的事实，该维度的信息充分度就高一分。

**② 支持规则 — 产生推断**

```yaml
support_rules:
  - field: ip_counts.patent    # 引用字段名
    op: ">="                   # 条件：大于等于
    value: 20                  # 阈值：20
    claim: 专利数量较多，可能存在研发管理需求。
```

如果企业的专利数 ≥ 20，系统就生成这条推断，标记置信度为"低"。

**③ 评分规则 — 影响产品推荐分数**

```yaml
positive_rules:
  - dimension_id: compliance_risk
    source_field: risk_counts.self   # 引用字段名
    op: ">="
    value: 20
    weight: 10                       # 命中后加多少分
    reason: 企业自身风险记录较多，合规管理产品更匹配。
```

如果企业的自身风险数 ≥ 20，该产品 +10 分。

### 如何确认某家企业有哪些字段、值是多少

```bash
# 跑一次离线推荐，输出目录下会生成 profile.json
uv run xft recommend --no-llm "企业名称"

# 查看该企业的完整画像
cat recommendation_runs/<运行编号>/profile.json
```

`profile.json` 就是该企业所有字段的当前值。写配置前先看两眼，比凭空猜字段名靠谱。

### 注意事项

- 字段名必须**完全一致**（包括大小写和下划线），`employee_count` 不能写成 `employeeCount` 或 `employeecount`
- 引用了一个不存在的字段 → 不会报错，但维度分析结果会是"不充分"，规则永远不会命中
- 如果 profile.json 里某字段为空或不存在，引用它的证据模板不会生成事实，规则不会命中——这意味着**数据源的质量直接影响推荐质量**

## 分数怎么算出来的

每个产品模块的最终分数由 **6 个组件** 加权求和得出，结果限制在 0-100 分之间（低于 0 按 0 计，超过 100 按 100 计）：

```
最终分数 = 基础分 + 维度得分 + 证据得分 + 补证得分 + 正向规则 - 缺失扣分 - 冲突扣分
```

### 1. 基础分（base_score）

产品优先级（配置中的 `priority`）乘以固定系数 0.45。优先级越高的产品，基础分越高。

> 例如：产品 A 优先级为 80，基础分 = 80 × 0.45 = 36 分。产品 B 优先级为 50，基础分 = 22.5 分。

### 2. 维度得分（dimension_support）

企业在每个分析维度下的信息充分程度，决定了该维度的状态：

**状态判定规则：**

系统在每个维度下配置了若干"证据模板"，将企业画像字段映射为事实陈述。例如，维度"人力资源"的模板可能包括：

- `employee_count` → "员工数量：850 人"
- `recruitment_count` → "招聘信息：2024 年共发布 32 条招聘记录"

然后按事实条数判定：

```
该维度下存在 ≥ 3 条事实  →  supported（充分支持）
该维度下存在 1-2 条事实  →  partial（部分支持）
该维度下存在 0 条事实    →  insufficient（不充分）
```

阈值 `3` 是默认值，可在 `config/evidence_policy.yaml` 中调整（`dimension_analysis.supported_facts_threshold`）。

**置信度映射：**

| 维度状态 | 置信度 | 含义 |
|---------|--------|------|
| supported | 中 | 本地有足够信息支撑判断，可靠性较高 |
| partial | 低 | 本地有部分信息但不够充分，建议开启 Web 补证 |
| insufficient | 待补充 | 本地无相关信息，**强烈建议**开启 Web 补证 |

**特殊规则：** 即使维度事实数为 0，某些维度仍会基于企业基本属性生成"弱推测"（inference）。例如：制造行业 + 员工数 ≥ 200 → 推测存在供应链管理需求。弱推测的置信度低于事实证据，不计入 `supported` 判定。

**评分影响：**

- 每个 `supported` 维度：+22 分
- 每个 `partial` 维度：+10 分
- `insufficient` 维度：不计分

### 3. 证据得分（evidence_support）

来自本地数据（Prophet JSON）的证据条目，每条 +4 分，上限 20 分。

**含义**：本地数据中有多少可验证的事实支撑这个推荐方向。

### 4. 补证得分（web_support）

来自 Web 搜索的外部证据：
- 每有一条"确认"本地信息的 Web 证据：+3 分（上限 12 分）
- 每有一条"补充"本地信息的 Web 证据：+1 分（上限 8 分）
- "冲突"的 Web 证据不计入此栏

**含义**：外部信息在多大程度上印证了本地画像。

### 5. 正向/负向规则（positive_rules / negative_rules）

配置文件中可以为每个产品定义规则，显式比对企业的画像字段：

- **正向规则**：满足条件 → 加分
- **负向规则**：满足条件 → 扣分

每条规则包含：引用哪个字段（`source_field`）、用什么比较方式（`op`）、阈值是多少（`value`）、命中后加/扣多少分（`weight` 或 `penalty`）。

**支持的全部比较方式：**

| 操作符 | 含义 | 适用字段类型 | 示例 |
|--------|------|-------------|------|
| `==` | 等于 | 数字、文字 | `reg_status == "注销"` → 排除 |
| `!=` | 不等于 | 数字、文字 | `company_org_type != "个体工商户"` |
| `>` | 大于 | 数字 | `employee_count > 500` |
| `>=` | 大于等于 | 数字 | `ip_counts.patent >= 20` |
| `<` | 小于 | 数字 | `employee_count < 50` |
| `<=` | 小于等于 | 数字 | `branch_count <= 1` |
| `exists` | 字段有值（非空非零） | 任意 | `website` 存在 → 有官网 |
| `contains` | 包含指定文字 | 文字、列表 | `business_scope` 包含 "制造"；`recent_recruitment_titles` 包含 "IT" |

**一个完整示例：**

```yaml
# 产品"合规风险管理"的正向规则
positive_rules:
  - dimension_id: compliance_risk
    source_field: risk_counts.self     # 看企业的自身风险数
    op: ">="
    value: 20
    weight: 10                          # 满足 → +10 分
    reason: 企业自身风险记录 ≥ 20 条，合规管理需求明确。

  - dimension_id: compliance_risk
    source_field: is_listed
    op: "=="
    value: true
    weight: 8                           # 满足 → +8 分
    reason: 上市公司合规披露要求更高。
```

这个产品如果企业自身风险 ≥ 20 且是上市公司，就能拿到 +18 分的规则加分。每条命中的规则都有记录，可以在 `result.json` 的 `score_breakdown.matched_rules` 里查到。如果规则没命中，这部分就是 0 分。

### 6. 缺失扣分与冲突扣分

- **缺失扣分**：分析维度中缺证据的条目，每条扣 1 分，上限扣 15 分
- **冲突扣分**：本地数据与 Web 数据存在矛盾，每处扣 8 分，无上限

### 排除规则

如果企业命中某产品的排除规则，该产品分数**强制封顶 20 分**（满分 100 的情况下 20 分意味着不推荐），报告中标注"不推荐"。

排除规则的作用是：**有些产品对某些企业类型天然不适用，不需要参与评分排名。**

例如：个体工商户不太可能需要"集团财务合并"类产品：

```yaml
exclusion_rules:
  - source_field: company_org_type
    op: "=="
    value: "个体工商户"
    reason: 个体工商户不适用集团财务合并产品。
```

## 规则引擎 vs 纯 LLM 判断：为什么不用 AI 直接判断

如果完全依赖 LLM 做推荐，存在三个致命问题：

1. **不可复现**：同一家企业连续跑两次，LLM 可能给出不同的分数和排序
2. **不可审计**：无法追溯"为什么推荐 A 不推荐 B"的判断依据——LLM 只会说"综合来看"
3. **幻觉**：LLM 可能引用不存在的数据作为推荐理由，看起来很合理但不真实

当前架构的设计原则是：

> **该确定的事情，交给规则引擎——用显式的操作符、阈值、公式做比对，结果可复现、可追溯。**
> **该灵活的事情，交给 LLM——把规则引擎产出的结构化结果，翻译成业务人员能直接用的销售话术。**

## `--no-llm` 下的确定性保证

当使用 `--no-llm` 参数时：

- 推荐分数：**100% 确定性**，同一家企业 + 同样的数据 → 同样的分数
- 推荐排序：按分数降序，分数相同时按产品优先级
- match 判断：由规则引擎计算（非大模型）
- 文案：由模板拼装（非大模型生成）
- 不消耗 API 额度，运行速度更快

**这是校准和调优场景下推荐使用的模式。** 只有在此模式下，两次运行的结果才可严格对比。日常使用可以去掉 `--no-llm` 让大模型润色话术。

## 如何验证推荐质量

### 校准流程

1. 准备一份标注文件（CSV 格式），列出"哪家企业应该推荐哪个产品"的标准答案
2. 对标注文件中的企业批量运行推荐（系统自动使用 `--no-llm` 保证确定性）
3. 系统自动对比推荐结果与标准答案，生成校准报告

```bash
uv run xft calibrate --labels calibration_labels.csv
```

校准报告包含：
- 每个产品的精确率、召回率、F1 分数
- 过评分/欠评分检测（某产品分高了还是分低了）
- Web 证据覆盖率

### 调优路径

校准报告会指出哪些产品的规则不够精准。对照报告结果，修改对应 YAML 文件中的规则阈值和权重，重新跑校准对比变化。循环迭代直到满意。具体改哪个文件见上方"调优时改哪些文件"表格。

## 调优时改哪些文件

所有推荐行为由 YAML 配置文件控制，不需要改代码：

| 想调整的内容 | 去哪个文件 |
|-------------|-----------|
| 评分公式中各部分的权重（如"维度得分每个 supported 加几分"） | `config/scoring_policy.yaml` |
| 维度状态判定阈值（几条事实算 supported）、证据质量判定阈值 | `config/evidence_policy.yaml` |
| 产品的正向/负向/排除规则（加什么条件、加几分） | `config/scenarios/<场景名>/products.yaml` |
| 分析维度的证据模板和支持规则（从哪些字段生成事实） | `config/scenarios/<场景名>/analysis_dimensions.yaml` |
| Web 搜索策略（什么情况下触发搜索、搜什么关键词） | `config/scenarios/<场景名>/web_search.yaml` |

修改 → 跑 `xft recommend --no-llm "测试企业"` → 看 `result.json` 里的分数和命中规则 → 再调整。重复这个循环直到满意。跑 `xft calibrate` 可以批量验证调优效果。

## 变更日志

| 日期 | 说明 |
|------|------|
| 2026-05-18 | 初始版本 |
