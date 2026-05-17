"""Small data structures for warehouse ETL."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompanyPackage:
    """A discovered Prophet enterprise data directory."""

    credit_code: str
    company_name: str
    directory_name: str
    json_files: tuple[str, ...]


@dataclass
class ImportSummary:
    """Summary returned by the Prophet JSON to DuckDB importer."""

    companies: int = 0
    raw_json_rows: int = 0
    import_status_counts: dict[str, int] = field(default_factory=dict)
    table_rows: dict[str, int] = field(default_factory=dict)
