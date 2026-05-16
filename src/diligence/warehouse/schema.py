"""DuckDB schema for the Prophet JSON warehouse."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TABLES_IN_LOAD_ORDER: tuple[str, ...] = (
    "raw_company_json",
    "company_import_status",
    "companies",
    "company_labels",
    "key_personnel",
    "shareholders",
    "ip_summary",
    "risk_features",
    "recruitments",
    "bidding_summary",
    "qualifications",
    "branches",
    "financing_events",
    "outbound_investments",
    "company_profile",
)


DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS raw_company_json (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_import_status (
      credit_code TEXT PRIMARY KEY,
      company_name TEXT NOT NULL,
      directory_name TEXT NOT NULL,
      json_file_count INTEGER NOT NULL,
      non_meta_json_file_count INTEGER NOT NULL,
      expected_v1_files JSON NOT NULL,
      missing_v1_files JSON NOT NULL,
      import_status TEXT NOT NULL,
      ingested_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS companies (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_labels (
      credit_code TEXT NOT NULL,
      label_name TEXT NOT NULL,
      label_code TEXT,
      label_class TEXT,
      label_type TEXT,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS key_personnel (
      credit_code TEXT NOT NULL,
      person_name TEXT NOT NULL,
      role TEXT,
      affiliate_company_count INTEGER,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shareholders (
      credit_code TEXT NOT NULL,
      shareholder_name TEXT NOT NULL,
      subscribe_amount TEXT,
      paid_amount TEXT,
      investment_rate DOUBLE,
      proportion TEXT,
      investor_type TEXT,
      listed BOOLEAN,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ip_summary (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_features (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recruitments (
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
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bidding_summary (
      credit_code TEXT PRIMARY KEY,
      total_count INTEGER,
      winner_count INTEGER,
      inviting_count INTEGER,
      latest_items JSON,
      source_files JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS qualifications (
      credit_code TEXT NOT NULL,
      qualification_name TEXT NOT NULL,
      qualification_type TEXT,
      level_name TEXT,
      publish_date TIMESTAMP,
      valid_from TIMESTAMP,
      valid_to TIMESTAMP,
      issuing_org TEXT,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS branches (
      credit_code TEXT NOT NULL,
      branch_name TEXT NOT NULL,
      branch_credit_code TEXT,
      reg_status TEXT,
      legal_person TEXT,
      established_at TIMESTAMP,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS financing_events (
      credit_code TEXT NOT NULL,
      event_date TIMESTAMP,
      financing_round TEXT,
      financing_amount TEXT,
      product TEXT,
      title TEXT,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbound_investments (
      credit_code TEXT NOT NULL,
      invested_company_name TEXT NOT NULL,
      amount DOUBLE,
      proportion TEXT,
      reg_status TEXT,
      source_file TEXT NOT NULL,
      raw_json JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_profile (
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
    )
    """,
)


def create_schema(conn: Any) -> None:
    """Create all warehouse tables."""
    for ddl in DDL:
        conn.execute(ddl)


def clear_tables(conn: Any, tables: Iterable[str] = TABLES_IN_LOAD_ORDER) -> None:
    """Delete warehouse table contents in dependency-safe order."""
    for table in reversed(tuple(tables)):
        conn.execute(f"DELETE FROM {table}")  # noqa: S608

