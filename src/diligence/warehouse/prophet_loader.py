"""Load Prophet enterprise JSON directories into the DuckDB warehouse."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from diligence.warehouse import adapters
from diligence.warehouse.duckdb_client import connect
from diligence.warehouse.models import CompanyPackage, ImportSummary
from diligence.warehouse.schema import TABLES_IN_LOAD_ORDER, clear_tables, create_schema

CREDIT_CODE_LENGTH = 18


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_company_dir(path: Path) -> bool:
    code, sep, name = path.name.partition("_")
    return path.is_dir() and bool(sep) and bool(name) and len(code) == CREDIT_CODE_LENGTH and code.isalnum()


def discover_company_packages(input_root: str | Path) -> list[CompanyPackage]:
    """Discover Prophet enterprise packages under *input_root*."""
    root = Path(input_root)
    packages: list[CompanyPackage] = []
    for directory in sorted(root.iterdir()):
        if not _is_company_dir(directory):
            continue
        code, _, name = directory.name.partition("_")
        meta_path = directory / ".meta.json"
        company_name = name
        credit_code = code
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
            if isinstance(meta, dict):
                company_name = str(meta.get("company_name") or company_name)
                credit_code = str(meta.get("credit_code") or credit_code)
        json_files = tuple(sorted(path.name for path in directory.glob("*.json")))
        packages.append(
            CompanyPackage(
                credit_code=credit_code,
                company_name=company_name,
                directory_name=directory.name,
                json_files=json_files,
            )
        )
    return packages


def _source_name(filename: str) -> str:
    if filename == ".meta.json":
        return "meta"
    return filename.removesuffix(".json")


def _load_meta(directory: Path) -> dict[str, Any]:
    meta_path = directory / ".meta.json"
    if not meta_path.exists():
        return {}
    try:
        value = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _fetched_at(meta: dict[str, Any], filename: str) -> datetime | None:
    fetchers = meta.get("fetchers")
    if not isinstance(fetchers, dict):
        return None
    item = fetchers.get(_source_name(filename))
    if not isinstance(item, dict):
        return None
    return adapters.parse_dt(item.get("fetched_at"))


def _load_raw_files(directory: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    parsed: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append({"source_file": path.name, "error": str(exc)})
            continue
        if isinstance(value, dict):
            parsed[path.name] = value
        else:
            parsed[path.name] = {"_value": value}
    return parsed, errors


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_rows(conn: Any, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    columns = list(rows[0])
    placeholders = ", ".join(["?"] * len(columns))
    column_sql = ", ".join(columns)
    values = [[row.get(column) for column in columns] for row in rows]
    conn.executemany(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})", values)  # noqa: S608
    return len(rows)


def _raw_rows(
    package: CompanyPackage,
    directory: Path,
    raw_files: dict[str, dict[str, Any]],
    parse_errors: list[dict[str, Any]],
    ingested_at: datetime,
) -> list[dict[str, Any]]:
    meta = raw_files.get(".meta.json", {})
    rows: list[dict[str, Any]] = []
    error_by_file = {str(err["source_file"]): str(err["error"]) for err in parse_errors}
    for path in sorted(directory.glob("*.json")):
        parsed = raw_files.get(path.name, {})
        rows.append(
            {
                "credit_code": package.credit_code,
                "company_name": package.company_name,
                "source_file": path.name,
                "source_name": _source_name(path.name),
                "raw_json": adapters.json_text(parsed),
                "fetched_at": _fetched_at(meta, path.name),
                "ingested_at": ingested_at,
                "file_size_bytes": path.stat().st_size,
                "content_hash": _content_hash(path),
                "parse_status": "failed" if path.name in error_by_file else "success",
                "parse_error": error_by_file.get(path.name),
            }
        )
    return rows


def _status_row(
    package: CompanyPackage,
    raw_files: dict[str, dict[str, Any]],
    ingested_at: datetime,
) -> tuple[dict[str, Any], str, list[str]]:
    names = set(raw_files)
    status, missing = adapters.import_status(names)
    row = {
        "credit_code": package.credit_code,
        "company_name": package.company_name,
        "directory_name": package.directory_name,
        "json_file_count": len(names),
        "non_meta_json_file_count": len([name for name in names if name != ".meta.json"]),
        "expected_v1_files": adapters.json_text(list(adapters.V1_EXPECTED_FILES)),
        "missing_v1_files": adapters.json_text(missing),
        "import_status": status,
        "ingested_at": ingested_at,
    }
    return row, status, missing


def _load_one_company(
    conn: Any,
    input_root: Path,
    package: CompanyPackage,
    ingested_at: datetime,
) -> dict[str, int]:
    directory = input_root / package.directory_name
    raw_files, parse_errors = _load_raw_files(directory)
    counts: Counter[str] = Counter()

    counts["raw_company_json"] += _insert_rows(
        conn,
        "raw_company_json",
        _raw_rows(package, directory, raw_files, parse_errors, ingested_at),
    )
    status, import_status_value, missing = _status_row(package, raw_files, ingested_at)
    counts["company_import_status"] += _insert_rows(conn, "company_import_status", [status])

    company = adapters.build_company_row(package.credit_code, package.company_name, raw_files, ingested_at)
    labels = adapters.build_label_rows(package.credit_code, raw_files)
    personnel = adapters.build_key_personnel_rows(package.credit_code, raw_files)
    shareholders = adapters.build_shareholder_rows(package.credit_code, raw_files)
    ip = adapters.build_ip_summary_row(package.credit_code, raw_files)
    risk = adapters.build_risk_features_row(package.credit_code, raw_files)
    recruitments = adapters.build_recruitment_rows(package.credit_code, raw_files)
    bidding = adapters.build_bidding_summary_row(package.credit_code, raw_files)
    qualifications = adapters.build_qualification_rows(package.credit_code, raw_files)
    branches = adapters.build_branch_rows(package.credit_code, raw_files)
    financing_events = adapters.build_financing_event_rows(package.credit_code, raw_files)
    outbound_investments = adapters.build_outbound_investment_rows(package.credit_code, raw_files)
    profile = adapters.build_profile_row(
        company=company,
        labels=labels,
        ip=ip,
        risk=risk,
        recruitments=recruitments,
        bidding=bidding,
        branches=branches,
        shareholders=shareholders,
        qualifications=qualifications,
        financing_events=financing_events,
        outbound_investments=outbound_investments,
        missing_v1_files=missing,
        import_status_value=import_status_value,
        raw_files=raw_files,
        updated_at=ingested_at,
    )

    counts["companies"] += _insert_rows(conn, "companies", [company])
    counts["company_labels"] += _insert_rows(conn, "company_labels", labels)
    counts["key_personnel"] += _insert_rows(conn, "key_personnel", personnel)
    counts["shareholders"] += _insert_rows(conn, "shareholders", shareholders)
    counts["ip_summary"] += _insert_rows(conn, "ip_summary", [ip])
    counts["risk_features"] += _insert_rows(conn, "risk_features", [risk])
    counts["recruitments"] += _insert_rows(conn, "recruitments", recruitments)
    counts["bidding_summary"] += _insert_rows(conn, "bidding_summary", [bidding])
    counts["qualifications"] += _insert_rows(conn, "qualifications", qualifications)
    counts["branches"] += _insert_rows(conn, "branches", branches)
    counts["financing_events"] += _insert_rows(conn, "financing_events", financing_events)
    counts["outbound_investments"] += _insert_rows(conn, "outbound_investments", outbound_investments)
    counts["company_profile"] += _insert_rows(conn, "company_profile", [profile])
    counts[f"status:{import_status_value}"] += 1
    return dict(counts)


def load_prophet_data(
    *,
    input_root: str | Path,
    output_db: str | Path,
    rebuild: bool = True,
) -> ImportSummary:
    """Load Prophet JSON packages into a DuckDB warehouse."""
    input_path = Path(input_root)
    if not input_path.exists():
        msg = f"input path not found: {input_path}"
        raise FileNotFoundError(msg)

    packages = discover_company_packages(input_path)
    conn = connect(output_db)
    try:
        create_schema(conn)
        if rebuild:
            clear_tables(conn)
        ingested_at = _now()
        summary = ImportSummary(companies=len(packages))
        aggregate: Counter[str] = Counter()
        conn.execute("BEGIN TRANSACTION")
        try:
            for package in packages:
                aggregate.update(_load_one_company(conn, input_path, package, ingested_at))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        summary.raw_json_rows = aggregate.get("raw_company_json", 0)
        summary.import_status_counts = {
            key.removeprefix("status:"): value for key, value in aggregate.items() if key.startswith("status:")
        }
        summary.table_rows = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
            for table in TABLES_IN_LOAD_ORDER
        }
        return summary
    finally:
        conn.close()
