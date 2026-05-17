"""Read company profiles from the DuckDB warehouse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

JSON_FIELDS = {
    "labels",
    "raw_label_codes",
    "bank_flags",
    "cross_border_flags",
    "ip_counts",
    "risk_counts",
    "recent_recruitment_titles",
    "shareholder_summary",
    "source_files",
    "missing_v1_files",
}


class CompanyProfileRepository:
    """Small repository around `company_profile`."""

    def __init__(self, warehouse_db: str | Path):
        self.warehouse_db = str(warehouse_db)

    def get_by_company_name(self, company_name: str) -> dict[str, Any] | None:
        conn = duckdb.connect(self.warehouse_db, read_only=True)
        try:
            exact = self._fetch_one(
                conn,
                "select * from company_profile where company_name = ? limit 1",
                [company_name],
            )
            if exact:
                return exact
            return self._fetch_one(
                conn,
                "select * from company_profile where company_name like ? order by profile_completeness desc limit 1",
                [f"%{company_name}%"],
            )
        finally:
            conn.close()

    @staticmethod
    def _fetch_one(conn: duckdb.DuckDBPyConnection, sql: str, params: list[str]) -> dict[str, Any] | None:
        result = conn.execute(sql, params)
        row = result.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in result.description]
        profile = dict(zip(columns, row, strict=True))
        for field in JSON_FIELDS:
            if isinstance(profile.get(field), str):
                try:
                    profile[field] = json.loads(profile[field])
                except json.JSONDecodeError:
                    profile[field] = None
        return profile
