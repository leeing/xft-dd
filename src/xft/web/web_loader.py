"""Load data/web cache artifacts into DuckDB Web tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xft.constants import DEFAULT_WAREHOUSE
from xft.evidence.models import normalize_resolution
from xft.utils.file_io import read_json, read_jsonl
from xft.warehouse.duckdb_client import connect
from xft.warehouse.schema import UNIFIED_EVIDENCE_DDL
from xft.web.models import WebLoadSummary

WEB_TABLES: tuple[str, ...] = (
    "web_search_runs",
    "web_search_queries",
    "web_search_results",
    "web_pages",
    "web_evidence",
)

WEB_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS web_search_runs (
      web_run_id TEXT PRIMARY KEY,
      credit_code TEXT,
      company_name TEXT NOT NULL,
      cache_dir TEXT NOT NULL,
      providers JSON NOT NULL,
      dimensions JSON NOT NULL,
      status TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL,
      manifest_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_search_queries (
      query_id TEXT NOT NULL,
      web_run_id TEXT NOT NULL,
      credit_code TEXT,
      company_name TEXT NOT NULL,
      dimension_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      query TEXT NOT NULL,
      status TEXT NOT NULL,
      raw_response_path TEXT,
      error TEXT,
      created_at TIMESTAMP NOT NULL,
      PRIMARY KEY (web_run_id, query_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_search_results (
      result_id TEXT NOT NULL,
      web_run_id TEXT NOT NULL,
      query_id TEXT NOT NULL,
      credit_code TEXT,
      company_name TEXT NOT NULL,
      dimension_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      title TEXT,
      url TEXT,
      snippet TEXT,
      full_text TEXT,
      full_text_preview TEXT,
      content_hash TEXT,
      page_path TEXT,
      source TEXT,
      rank INTEGER,
      raw_response_path TEXT,
      created_at TIMESTAMP NOT NULL,
      PRIMARY KEY (web_run_id, result_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_pages (
      page_id TEXT PRIMARY KEY,
      web_run_id TEXT NOT NULL,
      result_id TEXT NOT NULL,
      url TEXT,
      title TEXT,
      content_hash TEXT,
      page_path TEXT,
      metadata_path TEXT,
      text_length INTEGER,
      status TEXT,
      error TEXT,
      created_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_evidence (
      evidence_id TEXT NOT NULL,
      web_run_id TEXT NOT NULL,
      result_id TEXT,
      query_id TEXT NOT NULL,
      credit_code TEXT,
      company_name TEXT NOT NULL,
      dimension_id TEXT NOT NULL,
      provider TEXT NOT NULL,
      claim TEXT NOT NULL,
      evidence_type TEXT NOT NULL,
      relation_to_profile TEXT NOT NULL,
      confidence TEXT NOT NULL,
      source_url TEXT,
      source_title TEXT,
      query TEXT NOT NULL,
      source_quote TEXT,
      json_field TEXT,
      json_value TEXT,
      web_value TEXT,
      conflict_note TEXT,
      resolution TEXT,
      extraction_model TEXT,
      extraction_prompt_version TEXT,
      raw_response_path TEXT,
      created_at TIMESTAMP NOT NULL,
      PRIMARY KEY (web_run_id, evidence_id)
    )
    """,
)


def create_web_schema(conn: Any) -> None:
    for ddl in WEB_DDL:
        conn.execute(ddl)
    conn.execute(UNIFIED_EVIDENCE_DDL)
    _migrate_web_schema(conn)


def _migrate_web_schema(conn: Any) -> None:
    migrations = (
        "ALTER TABLE web_search_results ADD COLUMN IF NOT EXISTS full_text_preview TEXT",
        "ALTER TABLE web_search_results ADD COLUMN IF NOT EXISTS content_hash TEXT",
        "ALTER TABLE web_search_results ADD COLUMN IF NOT EXISTS page_path TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS relation_to_profile TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS source_quote TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS json_field TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS json_value TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS web_value TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS conflict_note TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS resolution TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS extraction_model TEXT",
        "ALTER TABLE web_evidence ADD COLUMN IF NOT EXISTS extraction_prompt_version TEXT",
    )
    for sql in migrations:
        conn.execute(sql)


def load_web_cache_to_duckdb(
    *,
    input_root: str | Path = "data/web",
    warehouse_db: str | Path = DEFAULT_WAREHOUSE,
    rebuild: bool = False,
) -> WebLoadSummary:
    """Load all data/web run directories into DuckDB Web tables."""
    root = Path(input_root)
    conn = connect(warehouse_db)
    try:
        create_web_schema(conn)
        if rebuild:
            for table in reversed(WEB_TABLES):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
            conn.execute("DELETE FROM unified_evidence WHERE source_type = 'web'")
        runs = queries = results = pages = evidence = 0
        for run_dir in _iter_run_dirs(root):
            manifest = read_json(run_dir / "manifest.json")
            if not manifest:
                continue
            _insert_run(conn, run_dir, manifest)
            runs += 1
            for row in read_jsonl(run_dir / "queries.jsonl"):
                _insert_query(conn, row)
                queries += 1
            for row in read_jsonl(run_dir / "search_results.jsonl"):
                _insert_result(conn, row)
                results += 1
            for row in read_jsonl(run_dir / "fetched_pages.jsonl"):
                _insert_page(conn, row)
                pages += 1
            for row in read_jsonl(run_dir / "web_evidence.jsonl"):
                _insert_evidence(conn, row)
                _insert_unified_web_evidence(conn, row)
                evidence += 1
        table_rows = {table: _count_rows(conn, table) for table in WEB_TABLES}
        table_rows["unified_evidence"] = int(conn.execute("SELECT count(*) FROM unified_evidence").fetchone()[0])
        return WebLoadSummary(runs=runs, queries=queries, results=results, evidence=evidence, table_rows=table_rows)
    finally:
        conn.close()


def _iter_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path.parent for path in root.rglob("manifest.json") if path.parent.is_dir())


def _count_rows(conn: Any, table: str) -> int:
    if table not in WEB_TABLES:
        msg = f"unknown web table: {table}"
        raise ValueError(msg)
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _insert_run(conn: Any, run_dir: Path, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO web_search_runs
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["web_run_id"],
            row.get("credit_code"),
            row["company_name"],
            str(run_dir),
            _json_text(row.get("providers", [])),
            _json_text(row.get("dimensions", [])),
            row["status"],
            row["created_at"],
            _json_text(row),
        ],
    )


def _insert_query(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO web_search_queries
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["query_id"],
            row["web_run_id"],
            row.get("credit_code"),
            row["company_name"],
            row["dimension_id"],
            row["provider"],
            row["query"],
            row["status"],
            row.get("raw_response_path"),
            row.get("error"),
            row["created_at"],
        ],
    )


def _insert_result(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO web_search_results (
          result_id, web_run_id, query_id, credit_code, company_name, dimension_id, provider,
          title, url, snippet, full_text, full_text_preview, content_hash, page_path, source,
          rank, raw_response_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["result_id"],
            row["web_run_id"],
            row["query_id"],
            row.get("credit_code"),
            row["company_name"],
            row["dimension_id"],
            row["provider"],
            row.get("title"),
            row.get("url"),
            row.get("snippet"),
            row.get("full_text", ""),
            row.get("full_text_preview", ""),
            row.get("content_hash"),
            row.get("page_path"),
            row.get("source"),
            row.get("rank"),
            row.get("raw_response_path"),
            row["created_at"],
        ],
    )


def _insert_page(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO web_pages
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["page_id"],
            row["web_run_id"],
            row["result_id"],
            row.get("url"),
            row["title"],
            row.get("content_hash"),
            row.get("page_path"),
            row.get("metadata_path"),
            row.get("text_length", 0),
            row["status"],
            row.get("error"),
            row["created_at"],
        ],
    )


def _insert_evidence(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO web_evidence (
          evidence_id, web_run_id, result_id, query_id, credit_code, company_name, dimension_id,
          provider, claim, evidence_type, relation_to_profile, confidence, source_url, source_title,
          query, source_quote, json_field, json_value, web_value, conflict_note, resolution,
          extraction_model, extraction_prompt_version, raw_response_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["evidence_id"],
            row["web_run_id"],
            row.get("result_id"),
            row["query_id"],
            row.get("credit_code"),
            row["company_name"],
            row["dimension_id"],
            row["provider"],
            row["claim"],
            row["evidence_type"],
            row.get("relation_to_profile", row["evidence_type"]),
            row["confidence"],
            row.get("source_url"),
            row["source_title"],
            row["query"],
            row.get("source_quote"),
            row.get("json_field"),
            row.get("json_value"),
            row.get("web_value"),
            row.get("conflict_note"),
            row.get("resolution"),
            row.get("extraction_model"),
            row.get("extraction_prompt_version"),
            row.get("raw_response_path"),
            row["created_at"],
        ],
    )


def _insert_unified_web_evidence(conn: Any, row: dict[str, Any]) -> None:
    relation = row.get("relation_to_profile") or row.get("evidence_type") or "supplement"
    resolution = normalize_resolution(row.get("resolution"), is_conflict=relation == "conflict")
    conn.execute(
        """
        INSERT OR REPLACE INTO unified_evidence (
          evidence_id, credit_code, company_name, dimension_id, source_type, source_name,
          source_path, source_url, source_field, claim, value, confidence, authority_level,
          relation_to_profile, conflict_note, resolution, raw_ref, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"web:{row['web_run_id']}:{row['evidence_id']}",
            row.get("credit_code"),
            row["company_name"],
            row["dimension_id"],
            "web",
            row["provider"],
            row.get("raw_response_path"),
            row.get("source_url"),
            row.get("json_field"),
            row["claim"],
            row.get("web_value"),
            row["confidence"],
            "unknown",
            relation,
            row.get("conflict_note"),
            resolution,
            _json_text(
                {
                    "web_run_id": row.get("web_run_id"),
                    "result_id": row.get("result_id"),
                    "query_id": row.get("query_id"),
                    "query": row.get("query"),
                    "source_title": row.get("source_title"),
                    "json_value": row.get("json_value"),
                    "extraction_model": row.get("extraction_model"),
                    "extraction_prompt_version": row.get("extraction_prompt_version"),
                }
            ),
            row["created_at"],
        ],
    )
