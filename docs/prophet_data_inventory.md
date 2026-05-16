# Prophet Data Inventory and Boundary Notes

Date: 2026-05-16

## Scope

This document records phase 0 of the warehouse/recommender refactor: data inventory and boundary confirmation.

The Prophet data package is currently copied under the project `data/` directory. The previous root-level copy was moved out of the source root, and `.cache/` was intentionally not copied because it is not needed for enterprise profile ETL.

## Directory Rules

Enterprise data directories are identified under `data/` by this pattern:

```text
{18-char unified_social_credit_code}_{company_name}
```

Examples:

```text
91440606707539050R_广东德美精细化工集团股份有限公司
9144060628009483XQ_广东科德环保科技股份有限公司
```

No `.cache/` directory is present under `data/`. If it appears later, it should be treated as Prophet request/cache data and excluded from enterprise profile ETL.

## Current Counts

```text
enterprise directories: 25
enterprise JSON files: 808
cache JSON files: 0
all JSON files under data depth 2: 808
min JSON files per enterprise: 1
max JSON files per enterprise: 42
unique enterprise JSON filenames: 47
```

The expected upper bound of about 45 JSON files per complete enterprise is consistent with the current sample. The observed filename universe is 47 because `.meta.json` and sparse/rare files such as `products.json`, `getbasinf.json`, and `query_fc_new.json` are included.

## Enterprise Completeness

Two enterprise directories currently contain only `.meta.json` and should be classified as `meta_only`:

```text
91441200632828391P 肇庆市宝信金属实业有限公司
91440606768435730J 佛山市顺德区惠尔家电器制品有限公司
```

The largest currently observed enterprise packages are:

```text
42 files 9144060628009483XQ 广东科德环保科技股份有限公司
41 files 91440600056815583U 辛格林电梯有限公司
40 files 91440606707539050R 广东德美精细化工集团股份有限公司
39 files 914406057081137139 广东宏正工程咨询有限公司
38 files 91440606712269577G 广东福田电器有限公司
38 files 91440606766570429D 广东海川智能机器股份有限公司
```

## JSON Filename Universe

```text
.meta.json
annoucement.json
annual_report.json
background.json
branch.json
brand.json
business_info.json
business_scope.json
certification.json
change.json
check.json
copyright.json
equity_structure.json
ext.json
getbasinf.json
import_export.json
info.json
insurances.json
intellectual.json
investor_info.json
judgement_detail.json
judgement_doc.json
label.json
land.json
licence.json
operation.json
partner.json
products.json
query_actual_control.json
query_base_label.json
query_bidding_total.json
query_company.json
query_company_bidding_new_inviting.json
query_company_bidding_new_winner.json
query_fc_new.json
query_investment_event_new.json
query_investor.json
query_qualification.json
record.json
recruit_message.json
relationship.json
risk_insight.json
shareholder.json
slow.json
software.json
staff.json
tax.json
```

## Common Files

These files appear in 23 of 25 enterprise directories. The two missing cases are the `meta_only` directories:

```text
ext.json
query_base_label.json
relationship.json
shareholder.json
background.json
licence.json
risk_insight.json
staff.json
annual_report.json
insurances.json
intellectual.json
slow.json
operation.json
info.json
label.json
query_company.json
business_scope.json
```

Other high-coverage files:

```text
22 recruit_message.json
22 partner.json
22 equity_structure.json
22 investor_info.json
22 change.json
21 query_qualification.json
20 query_bidding_total.json
20 tax.json
19 brand.json
19 business_info.json
18 judgement_doc.json
17 import_export.json
17 query_investor.json
```

## Core Files for V1 Profile

Recommended V1 profile inputs:

```text
.meta.json
info.json
query_company.json
label.json
intellectual.json
risk_insight.json
recruit_message.json
query_bidding_total.json
query_qualification.json
staff.json
shareholder.json
```

Known missing core files:

```text
91440101MA5BLXXQ34 孚慈汽车工业(广东)有限公司
missing: query_bidding_total.json, query_qualification.json

91440604MA51KAG48F 美世乐(广东)新能源科技有限公司
missing: query_bidding_total.json

914406057250934438 广东宝捷精密机械股份有限公司
missing: query_bidding_total.json

91440605MA57E2M39B 佛山凯洋医疗器械有限公司
missing: recruit_message.json

91440606MA4WLH6D9R 广东龙峰志达钢管制造有限公司
missing: query_qualification.json

91440606768435730J 佛山市顺德区惠尔家电器制品有限公司
missing: all V1 profile files except .meta.json

91441200632828391P 肇庆市宝信金属实业有限公司
missing: all V1 profile files except .meta.json
```

## Shape Notes

The files are not schema-uniform, but several common wrappers exist:

```text
{code, data, message, success, url}
{msg, code, data}
{company_name, labels, raw_label_codes}
```

Important V1 shapes:

```text
info.json
  data.info.info contains core registration fields such as name, businessScope,
  cate1/cate2/cate3, legalPersonName, regCapital, regLocation, regStatus,
  unifiedSocialCreditCode, listedCompanyState.

query_company.json
  data contains employeeNum/empNum, entName, industry fields, labels/flags,
  cross-border flags, bank/customer flags, contacts, coordinates.

label.json
  top-level labels and raw_label_codes.

intellectual.json
  data.intellectual is a list of IP category/count records.

risk_insight.json
  data.riskCount and related maps contain risk counters and details.

recruit_message.json
  data.list contains recruitment records.

query_bidding_total.json
  data.total contains bidding count summary.

query_qualification.json
  data is a list of qualification/certification records.

staff.json
  data.list contains key personnel.

shareholder.json
  data.list contains shareholder records.
```

## Boundary Decisions for Phase 1

1. Bronze import should ingest every JSON file inside enterprise directories, including `.meta.json`.
2. Bronze import should skip `.cache/` by default.
3. Bronze import should not require a complete set of JSON files. Missing files are normal.
4. Import status should distinguish at least:
   - `complete_or_rich`: enterprise has enough non-meta JSON for profile construction
   - `partial`: enterprise has some non-meta JSON but missing recommended V1 files
   - `meta_only`: enterprise has `.meta.json` only
5. Silver/Gold builders must be null-tolerant and source-aware.
6. `fetched_at` should be read from `.meta.json.fetchers[source_name].fetched_at` when available.
7. The default ETL input root should be `data/`.
8. The ETL may still accept a custom input path for future snapshots.
