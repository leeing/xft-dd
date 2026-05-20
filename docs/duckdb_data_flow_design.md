# DuckDB Data Flow Design for Prophet JSON

Date: 2026-05-16

## Goal

Build a local DuckDB warehouse from Prophet enterprise JSON packages in `data/`, then expose a compact `company_profile` Gold layer for recommendation scenarios.

The design is based on reading the current JSON contents directly. Files are incomplete by design: a rich enterprise package currently has up to 42 JSON files, while the expected upper bound is about 45. Missing JSON files must not fail the import.

## Input Contract

```text
data/
  {credit_code}_{company_name}/
    .meta.json
    info.json
    query_company.json
    ...
```

Directory recognition:

```text
{18-char unified_social_credit_code}_{company_name}
```

`.cache/` is not part of the input. If it appears in a later snapshot, skip it.

## Layer Overview

```text
Prophet JSON directories
  -> Bronze raw_company_json
  -> Silver normalized fact tables
  -> Gold company_profile
  -> Recommender reads company_profile only
```

Principles:

1. Preserve every source JSON in Bronze.
2. Parse only stable, useful fields into Silver V1.
3. Keep raw record JSON on every Silver row for audit/debug.
4. Gold is a denormalized, recommendation-friendly company profile.
5. Missing files produce NULLs or zero counts, not failures.
6. `source_files` and `profile_completeness` make data quality visible.

## Bronze Layer

### raw_company_json

Purpose: immutable-ish source archive and reprocessing base.

```sql
CREATE TABLE raw_company_json (
  credit_code TEXT NOT NULL,
  company_name TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_name TEXT NOT NULL,
  raw_json JSON NOT NULL,
  fetched_at TIMESTAMP,
  ingested_at TIMESTAMP NOT NULL,
  file_size_bytes BIGINT,
  content_hash TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  parse_error TEXT,
  PRIMARY KEY (credit_code, source_file)
);
```

`source_name` is the filename without `.json`, except `.meta.json` becomes `meta`.

`fetched_at` comes from `.meta.json.fetchers[source_name].fetched_at` when available.

### company_import_status

Purpose: one row per directory, used to audit incomplete packages.

```sql
CREATE TABLE company_import_status (
  credit_code TEXT PRIMARY KEY,
  company_name TEXT NOT NULL,
  directory_name TEXT NOT NULL,
  json_file_count INTEGER NOT NULL,
  non_meta_json_file_count INTEGER NOT NULL,
  expected_v1_files JSON NOT NULL,
  missing_v1_files JSON NOT NULL,
  import_status TEXT NOT NULL,
  ingested_at TIMESTAMP NOT NULL
);
```

Suggested `import_status`:

```text
complete_or_rich: has enough V1 files for profile construction
partial: has non-meta JSON but misses recommended V1 files
meta_only: only .meta.json exists
failed: directory/file parsing failed unexpectedly
```

V1 expected files:

```text
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

## Silver Layer V1

Silver V1 focuses on recommendation needs. It does not try to model all 47 file types immediately.

### companies

Sources:

```text
info.json -> data.info.info
query_company.json -> data
getbasinf.json -> data, only for listed-company enrichment
business_scope.json -> data.info fallback
ext.json -> data
```

Key field mapping:

```text
name                         info.data.info.info.name | query_company.data.entName
credit_code                  info.data.info.info.unifiedSocialCreditCode | directory code
industry                     query_company.data.idtCtgNm | info.data.info.info.cate1
industry_big                 query_company.data.industryBig | info.data.info.info.cate2
industry_mid                 info.data.info.info.cate3
industry_small               query_company.data.idtSmlClsNm
employee_count               query_company.data.employeeNum | query_company.data.empNum | latest insurances.people
registered_capital           info.data.info.info.regCapital
registered_capital_currency  query_company.data.regCapCur
registered_location          info.data.info.info.regLocation
province                     query_company.data.province
county                       query_company.data.county
business_scope               info.data.info.info.businessScope | business_scope.data.info
established_at               info.data.info.info.estiblishTime | query_company.data.establishDate
legal_person                 info.data.info.info.legalPersonName | query_company.data.legalName
reg_status                   info.data.info.info.regStatus
company_org_type             info.data.info.info.companyOrgType
listed_company_state         info.data.info.info.listedCompanyState
stock_code                   getbasinf.data.stkCd | info.data.info.info.secucode
stock_short_name             getbasinf.data.scrShtNm | info.data.info.info.secuabbr
website                      ext.data.website | getbasinf.data.cmpHmpg
email                        ext.data.email | first query_company.data.emailList
phones                       ext.data.businessPhone + query_company.data.phoneList
```

Table:

```sql
CREATE TABLE companies (
  credit_code TEXT PRIMARY KEY,
  company_name TEXT NOT NULL,
  industry TEXT,
  industry_big TEXT,
  industry_mid TEXT,
  industry_small TEXT,
  employee_count INTEGER,
  employee_count_source TEXT,
  registered_capital TEXT,
  registered_capital_currency TEXT,
  registered_location TEXT,
  province TEXT,
  county TEXT,
  business_scope TEXT,
  established_at TIMESTAMP,
  legal_person TEXT,
  reg_status TEXT,
  company_org_type TEXT,
  listed_company_state INTEGER,
  stock_code TEXT,
  stock_short_name TEXT,
  website TEXT,
  email TEXT,
  phones JSON,
  source_files JSON,
  raw_refs JSON,
  updated_at TIMESTAMP NOT NULL
);
```

### company_labels

Sources:

```text
label.json -> labels, raw_label_codes
query_base_label.json -> data[]
query_company.json -> Y/N flag fields and crossBorderLabel
```

Table:

```sql
CREATE TABLE company_labels (
  credit_code TEXT NOT NULL,
  label_name TEXT NOT NULL,
  label_code TEXT,
  label_class TEXT,
  label_type TEXT,
  source_file TEXT NOT NULL,
  raw_json JSON,
  PRIMARY KEY (credit_code, label_name, source_file)
);
```

Important boolean flags for Gold:

```text
highQualityCustomer == "Y"
creditGrantingCustomer == "Y"
chrFinInd == "Y"
hiOprValCustInd == "Y"
crossBorderSmallExportSalesMark == "Y"
crossBorderServiceTradeMark == "Y"
crossBorderSmallServiceTradeMark == "Y"
```

### key_personnel

Sources:

```text
staff.json -> data.list[]
slow.json -> data.staffExts[] with affiliateCompany
```

```sql
CREATE TABLE key_personnel (
  credit_code TEXT NOT NULL,
  person_name TEXT NOT NULL,
  role TEXT,
  affiliate_company_count INTEGER,
  source_file TEXT NOT NULL,
  raw_json JSON,
  PRIMARY KEY (credit_code, person_name, role, source_file)
);
```

### shareholders

Sources:

```text
shareholder.json -> data.list[]
equity_structure.json -> data[]
info.json -> data.info.partner[]
```

```sql
CREATE TABLE shareholders (
  credit_code TEXT NOT NULL,
  shareholder_name TEXT NOT NULL,
  subscribe_amount TEXT,
  paid_amount TEXT,
  investment_rate DOUBLE,
  proportion TEXT,
  investor_type TEXT,
  listed BOOLEAN,
  source_file TEXT NOT NULL,
  raw_json JSON,
  PRIMARY KEY (credit_code, shareholder_name, source_file)
);
```

### ip_summary

Sources:

```text
intellectual.json -> data.intellectual[]
brand.json -> data.total and data.list[]
partner.json -> data.total and data.list[] for patents
software.json -> data.total and data.list[]
copyright.json -> data.total and data.list[]
products.json -> data.total and data.list[]
record.json -> website filings
```

For V1, store category counts and selected latest names. Detailed patent/trademark tables can be added later.

```sql
CREATE TABLE ip_summary (
  credit_code TEXT PRIMARY KEY,
  trademark_count INTEGER,
  patent_count INTEGER,
  software_count INTEGER,
  works_copyright_count INTEGER,
  website_filing_count INTEGER,
  product_count INTEGER,
  latest_patent_titles JSON,
  latest_trademark_names JSON,
  source_files JSON,
  raw_refs JSON
);
```

### risk_features

Sources:

```text
risk_insight.json -> data.riskCount, data.riskInsight[]
business_info.json -> court sessions/list
judgement_doc.json -> legal judgement docs
judgement_detail.json -> optional details
annoucement.json -> court announcements
check.json -> inspection records
change.json -> registration changes
```

```sql
CREATE TABLE risk_features (
  credit_code TEXT PRIMARY KEY,
  self_risk_count INTEGER,
  pre_risk_count INTEGER,
  around_risk_count INTEGER,
  court_session_count INTEGER,
  judgement_doc_count INTEGER,
  announcement_count INTEGER,
  inspection_count INTEGER,
  change_count INTEGER,
  risk_categories JSON,
  source_files JSON,
  raw_refs JSON
);
```

### recruitments

Source:

```text
recruit_message.json -> data.list[]
```

```sql
CREATE TABLE recruitments (
  credit_code TEXT NOT NULL,
  title TEXT NOT NULL,
  city TEXT,
  district TEXT,
  education TEXT,
  experience TEXT,
  salary_text TEXT,
  employer_number TEXT,
  source TEXT,
  start_date TIMESTAMP,
  end_date TIMESTAMP,
  raw_json JSON,
  PRIMARY KEY (credit_code, title, source, start_date)
);
```

### bidding

Sources:

```text
query_bidding_total.json -> data.total
query_company_bidding_new_winner.json -> list[]
query_company_bidding_new_inviting.json -> list[]
```

```sql
CREATE TABLE bidding_summary (
  credit_code TEXT PRIMARY KEY,
  total_count INTEGER,
  winner_count INTEGER,
  inviting_count INTEGER,
  latest_items JSON,
  source_files JSON
);
```

### qualifications

Sources:

```text
query_qualification.json -> data[]
certification.json -> data.list[]
licence.json -> data.list[]
tax.json -> data.list[]
import_export.json -> data.creditInfo, data.creditRating[]
```

```sql
CREATE TABLE qualifications (
  credit_code TEXT NOT NULL,
  qualification_name TEXT NOT NULL,
  qualification_type TEXT,
  level_name TEXT,
  publish_date TIMESTAMP,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP,
  issuing_org TEXT,
  source_file TEXT NOT NULL,
  raw_json JSON,
  PRIMARY KEY (credit_code, qualification_name, source_file)
);
```

### branches

Source:

```text
branch.json -> data.list[]
relationship.json -> messageNo for 分支机构 summary
```

```sql
CREATE TABLE branches (
  credit_code TEXT NOT NULL,
  branch_name TEXT NOT NULL,
  branch_credit_code TEXT,
  reg_status TEXT,
  legal_person TEXT,
  established_at TIMESTAMP,
  raw_json JSON,
  PRIMARY KEY (credit_code, branch_name)
);
```

### investments_and_financing

Sources:

```text
query_investor.json -> outbound investments
query_actual_control.json -> control chain references
query_investment_event_new.json -> financing events
query_fc_new.json -> financing events
```

```sql
CREATE TABLE financing_events (
  credit_code TEXT NOT NULL,
  event_date TIMESTAMP,
  financing_round TEXT,
  financing_amount TEXT,
  product TEXT,
  title TEXT,
  source_file TEXT NOT NULL,
  raw_json JSON
);

CREATE TABLE outbound_investments (
  credit_code TEXT NOT NULL,
  invested_company_name TEXT NOT NULL,
  amount DOUBLE,
  proportion TEXT,
  reg_status TEXT,
  source_file TEXT NOT NULL,
  raw_json JSON,
  PRIMARY KEY (credit_code, invested_company_name, source_file)
);
```

## Gold Layer

### company_profile

This is the only table the V1 product recommender should read.

```sql
CREATE TABLE company_profile (
  credit_code TEXT PRIMARY KEY,
  company_name TEXT NOT NULL,
  industry TEXT,
  industry_big TEXT,
  industry_mid TEXT,
  industry_small TEXT,
  employee_count INTEGER,
  employee_count_source TEXT,
  registered_capital TEXT,
  registered_location TEXT,
  province TEXT,
  county TEXT,
  business_scope TEXT,
  established_at TIMESTAMP,
  legal_person TEXT,
  reg_status TEXT,
  company_org_type TEXT,
  is_listed BOOLEAN,
  stock_code TEXT,
  stock_short_name TEXT,
  website TEXT,
  labels JSON,
  raw_label_codes JSON,
  bank_flags JSON,
  cross_border_flags JSON,
  ip_counts JSON,
  risk_counts JSON,
  recruitment_count INTEGER,
  recent_recruitment_titles JSON,
  bidding_total INTEGER,
  branch_count INTEGER,
  shareholder_summary JSON,
  qualification_count INTEGER,
  financing_event_count INTEGER,
  outbound_investment_count INTEGER,
  source_files JSON,
  missing_v1_files JSON,
  profile_completeness DOUBLE,
  import_status TEXT,
  updated_at TIMESTAMP NOT NULL
);
```

Recommended `profile_completeness` fields:

```text
Required:
company_name
credit_code
industry or industry_big
employee_count
business_scope

Recommended:
labels
ip_counts
risk_counts
recruitment_count
bidding_total
qualification_count
shareholder_summary
```

Score suggestion:

```text
required_score = present_required / 5 * 0.65
recommended_score = present_recommended / 7 * 0.35
profile_completeness = required_score + recommended_score
```

## Transformation Flow

Phase 1 script:

```bash
uv run python etl_json_to_duckdb.py \
  --input data \
  --output cache/company_warehouse.duckdb
```

Steps:

1. Discover enterprise directories under `data/`.
2. Parse `.meta.json` first for company identity and fetch timestamps.
3. Insert every JSON file into `raw_company_json`.
4. Insert one row into `company_import_status`.
5. Build Silver tables from Bronze raw JSON.
6. Build or refresh `company_profile`.
7. Print an import summary: total companies, rich/partial/meta_only counts, row counts by table.

## Implementation Notes

Use Python for parsing, not ad hoc SQL JSON path extraction. The JSON shapes are inconsistent enough that explicit Python adapters will be easier to test and maintain.

Suggested modules:

```text
src/xft/warehouse/
  duckdb_client.py
  schema.py
  prophet_loader.py
  adapters.py
  profile_builder.py
  models.py
```

Suggested adapter style:

```text
parse_company_core(raw_files) -> companies row
parse_labels(raw_files) -> company_labels rows
parse_key_personnel(raw_files) -> key_personnel rows
parse_ip_summary(raw_files) -> ip_summary row
parse_risk_features(raw_files) -> risk_features row
...
```

Each adapter receives all available raw files for one company and returns zero or more rows. Missing files return empty rows/defaults.

## V1 Versus Later

V1 should prioritize the tables needed to support recommendation. These are:

```text
raw_company_json
company_import_status
companies
company_labels
key_personnel
shareholders
ip_summary
risk_features
recruitments
bidding_summary
qualifications
branches
financing_events
outbound_investments
company_profile
```

Later phases can split high-cardinality IP and legal records into richer detail tables:

```text
trademarks
patents
software_copyrights
works_copyrights
licences
tax_ratings
court_sessions
judgement_docs
company_changes
annual_reports
social_insurance
```
