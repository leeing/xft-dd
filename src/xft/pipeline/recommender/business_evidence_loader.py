"""Load indicator-scoped business evidence from profile fields and DuckDB tables."""

from __future__ import annotations

from typing import Any

import duckdb

from xft.pipeline.recommender.business_models import (
    BusinessDataSourceConfig,
    BusinessIndicatorConfig,
    BusinessModuleConfig,
    BusinessRecommendationConfig,
)
from xft.utils.misc import contains, get_nested

ALLOWED_TABLE_FIELDS: dict[str, set[str]] = {
    "recruitments": {
        "title",
        "city",
        "district",
        "education",
        "experience",
        "salary_text",
        "employer_number",
        "source",
    },
    "branches": {"branch_name", "reg_status", "legal_person"},
    "qualifications": {"qualification_name", "qualification_type", "level_name", "issuing_org"},
    "outbound_investments": {"invested_company_name", "proportion", "reg_status"},
    "key_personnel": {"person_name", "role", "affiliate_company_count"},
}


def indicator_key(module: BusinessModuleConfig, label_id: str, indicator: BusinessIndicatorConfig) -> str:
    """Return the stable key used for evidence maps and traces."""
    return f"{module.module_id}.{label_id}.{indicator.indicator_id}"


def load_business_evidence(
    *,
    config: BusinessRecommendationConfig | None,
    warehouse_db: str,
    profile: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Load evidence only for indicators that declare data_sources.

    The loader is intentionally conservative: SQL identifiers come from an
    allowlist and row filtering happens in Python, which keeps the YAML config
    expressive without turning it into arbitrary SQL.
    """
    if config is None:
        return {}
    credit_code = str(profile.get("credit_code") or "")
    if not credit_code:
        return {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    conn = duckdb.connect(warehouse_db, read_only=True)
    try:
        for module in config.modules:
            for label in module.labels:
                for indicator in label.indicators:
                    if not indicator.data_sources:
                        continue
                    key = indicator_key(module, label.label_id, indicator)
                    evidence[key] = [
                        item
                        for source in indicator.data_sources
                        for item in _load_source(conn, source=source, profile=profile, credit_code=credit_code)
                    ]
    finally:
        conn.close()
    return evidence


def _load_source(
    conn: duckdb.DuckDBPyConnection,
    *,
    source: BusinessDataSourceConfig,
    profile: dict[str, Any],
    credit_code: str,
) -> list[dict[str, Any]]:
    if source.type == "field":
        return [_field_evidence(source, profile)]
    if source.type == "table":
        return _table_evidence(conn, source=source, credit_code=credit_code)
    return []


def _field_evidence(source: BusinessDataSourceConfig, profile: dict[str, Any]) -> dict[str, Any]:
    value = get_nested(profile, source.path or "")
    matched = _source_matches(value=value, source=source)
    return {
        "source_type": "field",
        "source": source.path,
        "op": source.op,
        "expected": source.value if source.value is not None else source.keywords,
        "value": value,
        "matched": matched,
        "evidence": f"{source.path} = {_display_value(value)}" if value not in (None, "", [], {}) else "",
        "description": source.description,
    }


def _table_evidence(
    conn: duckdb.DuckDBPyConnection,
    *,
    source: BusinessDataSourceConfig,
    credit_code: str,
) -> list[dict[str, Any]]:
    table = source.table or ""
    field = source.field or ""
    allowed = ALLOWED_TABLE_FIELDS.get(table)
    if not allowed or field not in allowed:
        return [
            {
                "source_type": "table",
                "source": f"{table}.{field}",
                "matched": False,
                "evidence": "",
                "error": f"unsupported table field: {table}.{field}",
            }
        ]
    result = conn.execute(
        f"select * from {table} where credit_code = ? limit ?",  # noqa: S608 - identifiers are allowlisted above.
        [credit_code, source.limit],
    )
    columns = [desc[0] for desc in result.description]
    rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    matched_rows = [row for row in rows if _source_matches(value=row.get(field), source=source)]
    evidence = [
        {
            "source_type": "table",
            "source": f"{table}.{field}",
            "op": source.op,
            "expected": source.value if source.value is not None else source.keywords,
            "value": row.get(field),
            "matched": True,
            "evidence": f"{table}.{field} 命中: {_display_value(row.get(field))}",
            "row": _compact_row(row),
            "description": source.description,
        }
        for row in matched_rows[: source.limit]
    ]
    if evidence:
        return evidence
    return [
        {
            "source_type": "table",
            "source": f"{table}.{field}",
            "op": source.op,
            "expected": source.value if source.value is not None else source.keywords,
            "matched": False,
            "evidence": "",
            "sample_count": len(rows),
            "description": source.description,
        }
    ]


def _source_matches(*, value: Any, source: BusinessDataSourceConfig) -> bool:  # noqa: C901, PLR0911
    if source.op == "text_contains":
        keywords = source.keywords or ([str(source.value)] if source.value not in (None, "") else [])
        if not keywords:
            return value not in (None, "", [], {})
        return sum(1 for keyword in keywords if contains(value, keyword)) >= source.min_matches
    if source.op == "exists":
        return value not in (None, "", [], {})
    if source.op in ("contains", "contains_any"):
        expected = source.keywords or source.value
        if source.op == "contains_any":
            values = expected if isinstance(expected, list) else [expected]
            return any(contains(value, item) for item in values)
        return contains(value, expected)
    if source.op in ("==", "!="):
        result = _normalize(value) == _normalize(source.value)
        return result if source.op == "==" else not result
    left = _as_number(value)
    right = _as_number(source.value)
    if left is None or right is None:
        return False
    if source.op == ">":
        return left > right
    if source.op == ">=":
        return left >= right
    if source.op == "<":
        return left < right
    if source.op == "<=":
        return left <= right
    return False


def _normalize(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _display_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value[:6])
    if isinstance(value, dict):
        return "、".join(f"{key}:{val}" for key, val in list(value.items())[:6])
    return str(value)


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "raw_json" and value not in (None, "", [], {})}
