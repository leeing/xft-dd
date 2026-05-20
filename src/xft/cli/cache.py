"""CLI for cache maintenance tasks.

Usage:
    uv run xft cache sync-remote
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

import asyncpg  # type: ignore[import-untyped]
import duckdb
import structlog
from dotenv import load_dotenv

log = structlog.get_logger(__name__)

PG_URL_PARTS = 2
DEFAULT_LOCAL_DUCKDB_PATH = Path("cache/xft_cache.duckdb")


class PgConnectionInfo(NamedTuple):
    user: str
    password: str
    host: str
    port: int
    database: str
    ssl: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xft cache", description="manage XFT caches")
    subparsers = parser.add_subparsers(dest="command")
    sync_parser = subparsers.add_parser("sync-remote", help="sync remote PostgreSQL cache into local DuckDB")
    sync_parser.add_argument("--database-url", help="PostgreSQL URL; defaults to CACHE_DATABASE_URL")
    sync_parser.add_argument("--output", default=str(DEFAULT_LOCAL_DUCKDB_PATH), help="local DuckDB output path")
    return parser


def _parse_pg_url(pg_url: str) -> PgConnectionInfo:
    if "postgresql" not in pg_url:
        msg = "CACHE_DATABASE_URL does not point to PostgreSQL"
        raise ValueError(msg)
    normalized = pg_url.replace("postgresql+asyncpg://", "").replace("postgresql://", "")
    parts = normalized.split("@")
    if len(parts) != PG_URL_PARTS:
        msg = "cannot parse PostgreSQL URL"
        raise ValueError(msg)
    user_pass, host_db = parts
    user, password = user_pass.split(":", 1)
    host_db_parts = host_db.split("/")
    host_port = host_db_parts[0]
    db_part = host_db_parts[1] if len(host_db_parts) > 1 else "neondb"
    db_name = db_part.split("?")[0]
    ssl_arg = "?".join(db_part.split("?")[1:]) if "?" in db_part else ""
    host, raw_port = host_port.split(":", 1) if ":" in host_port else (host_port, "5432")
    return PgConnectionInfo(
        user=user,
        password=password,
        host=host,
        port=int(raw_port),
        database=db_name,
        ssl=bool(ssl_arg),
    )


def create_duckdb_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the same table structure in DuckDB."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            id INTEGER PRIMARY KEY,
            provider VARCHAR(32) NOT NULL,
            query_text TEXT NOT NULL,
            query_hash VARCHAR(64) NOT NULL,
            params_hash VARCHAR(64) NOT NULL,
            policy_version VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            raw_response_json TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            error TEXT,
            created_at TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_result_url (
            id INTEGER PRIMARY KEY,
            search_cache_id INTEGER NOT NULL,
            normalized_url TEXT,
            original_url TEXT,
            title TEXT NOT NULL,
            snippet TEXT NOT NULL,
            rank INTEGER,
            raw_item_json TEXT NOT NULL,
            created_at TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_cache (
            normalized_url TEXT PRIMARY KEY,
            original_url TEXT,
            final_url TEXT,
            source_type VARCHAR(64),
            authority_level VARCHAR(32),
            should_fetch_bias VARCHAR(32),
            status VARCHAR(32) NOT NULL,
            markdown TEXT,
            content_hash VARCHAR(64),
            error TEXT,
            fetched_at TIMESTAMP,
            expires_at TIMESTAMP,
            retry_count INTEGER NOT NULL DEFAULT 0,
            locked_by VARCHAR(128),
            locked_until TIMESTAMP,
            policy_version VARCHAR(64) NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)

    # Create indexes
    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_search_cache_lookup
        ON search_cache(provider, query_hash, params_hash)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_search_result_url_normalized
        ON search_result_url(normalized_url)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_fetch_cache_status
        ON fetch_cache(status)
    """)


_SEARCH_CACHE_COLUMNS = [
    "id",
    "provider",
    "query_text",
    "query_hash",
    "params_hash",
    "policy_version",
    "status",
    "raw_response_json",
    "result_count",
    "error",
    "created_at",
    "expires_at",
]


def _naive_row(row: dict[str, Any], columns: list[str]) -> list[Any]:
    record: list[Any] = []
    for col in columns:
        val = row[col]
        if hasattr(val, "tzinfo") and val.tzinfo is not None:
            val = val.replace(tzinfo=None)
        record.append(val)
    return record


async def sync_search_cache(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync search_cache table."""
    rows = await pg_conn.fetch("SELECT * FROM search_cache")
    if not rows:
        log.info("sync_search_cache: 0 rows (skip)")
        return 0

    columns = _SEARCH_CACHE_COLUMNS
    data = [_naive_row(dict(row), columns) for row in rows]
    placeholders = ", ".join(["?" for _ in columns])
    duck_conn.executemany(
        f"INSERT INTO search_cache ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
        data,
    )
    count = len(data)
    log.info("sync_search_cache", rows=count)
    return count


_SEARCH_RESULT_URL_COLUMNS = [
    "id",
    "search_cache_id",
    "normalized_url",
    "original_url",
    "title",
    "snippet",
    "rank",
    "raw_item_json",
    "created_at",
]


async def sync_search_result_url(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync search_result_url table."""
    rows = await pg_conn.fetch("SELECT * FROM search_result_url")
    if not rows:
        log.info("sync_search_result_url: 0 rows (skip)")
        return 0

    columns = _SEARCH_RESULT_URL_COLUMNS
    data = [_naive_row(dict(row), columns) for row in rows]
    placeholders = ", ".join(["?" for _ in columns])
    duck_conn.executemany(
        f"INSERT INTO search_result_url ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
        data,
    )
    count = len(data)
    log.info("sync_search_result_url", rows=count)
    return count


_FETCH_CACHE_COLUMNS = [
    "normalized_url",
    "original_url",
    "final_url",
    "source_type",
    "authority_level",
    "should_fetch_bias",
    "status",
    "markdown",
    "content_hash",
    "error",
    "fetched_at",
    "expires_at",
    "retry_count",
    "locked_by",
    "locked_until",
    "policy_version",
    "updated_at",
]


async def sync_fetch_cache(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync fetch_cache table."""
    rows = await pg_conn.fetch("SELECT * FROM fetch_cache")
    if not rows:
        log.info("sync_fetch_cache: 0 rows (skip)")
        return 0

    columns = _FETCH_CACHE_COLUMNS
    data = [_naive_row(dict(row), columns) for row in rows]
    placeholders = ", ".join(["?" for _ in columns])
    duck_conn.executemany(
        f"INSERT INTO fetch_cache ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
        data,
    )
    count = len(data)
    log.info("sync_fetch_cache", rows=count)
    return count


async def _sync_remote(args: argparse.Namespace) -> int:
    load_dotenv()
    pg_url = args.database_url or os.getenv("CACHE_DATABASE_URL", "")
    try:
        pg = _parse_pg_url(pg_url)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    local_duckdb_path = Path(args.output)
    log.info("connecting", pg_host=f"{pg.host}:{pg.port}", pg_db=pg.database, duckdb=str(local_duckdb_path))

    pg_conn = await asyncpg.connect(
        user=pg.user,
        password=pg.password,
        host=pg.host,
        port=pg.port,
        database=pg.database,
        ssl=pg.ssl,
    )

    local_duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    duck_conn = duckdb.connect(str(local_duckdb_path))
    create_duckdb_tables(duck_conn)

    log.info("syncing_tables")
    total = 0
    total += await sync_search_cache(pg_conn, duck_conn)
    total += await sync_search_result_url(pg_conn, duck_conn)
    total += await sync_fetch_cache(pg_conn, duck_conn)

    log.info("sync_complete", total_rows=total)

    await pg_conn.close()
    duck_conn.close()
    log.info("done", duckdb=str(local_duckdb_path))
    sys.stdout.write(f"synced_rows: {total}\n")
    sys.stdout.write(f"duckdb: {local_duckdb_path}\n")
    return 0


async def _main_async(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sync-remote":
        return await _sync_remote(args)
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
