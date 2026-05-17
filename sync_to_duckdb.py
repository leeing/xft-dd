"""Sync cache data from remote PostgreSQL to local DuckDB.

Usage:
    uv run python sync_to_duckdb.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import dotenv
import duckdb
import structlog

# Load .env file
dotenv.load_dotenv()

log = structlog.get_logger(__name__)

PG_URL_PARTS = 2


# PostgreSQL connection from .env
PG_URL = os.getenv("CACHE_DATABASE_URL", "")
if "postgresql" not in PG_URL:
    log.error("CACHE_DATABASE_URL does not point to PostgreSQL", url=PG_URL)
    sys.exit(1)

# Parse PG URL
# Format: postgresql+asyncpg://user:password@host:port/dbname?ssl=true
pg_url = PG_URL.replace("postgresql+asyncpg://", "").split("@")
if len(pg_url) != PG_URL_PARTS:
    log.error("Cannot parse PostgreSQL URL", url=PG_URL)
    sys.exit(1)

user_pass, host_db = pg_url
user, password = user_pass.split(":")
host_db_parts = host_db.split("/")
host_port = host_db_parts[0]
db_name = host_db_parts[1].split("?")[0] if "/" in host_db else "neondb"
ssl_arg = "?".join(host_db_parts[1].split("?")[1:]) if "?" in host_db_parts[1] else ""

LOCAL_DUCKDB_PATH = Path("cache/diligence_local.duckdb")


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


def _naive_row(row: dict, columns: list[str]) -> list:
    record: list = []
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


async def main() -> None:
    log.info("connecting", pg_host=host_port, pg_db=db_name, duckdb=str(LOCAL_DUCKDB_PATH))

    # Connect to PostgreSQL
    pg_conn = await asyncpg.connect(
        user=user,
        password=password,
        host=host_port.split(":")[0],
        port=int(host_port.split(":")[1]) if ":" in host_port else 5432,
        database=db_name,
        ssl=bool(ssl_arg),
    )

    # Create DuckDB
    LOCAL_DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    duck_conn = duckdb.connect(str(LOCAL_DUCKDB_PATH))
    create_duckdb_tables(duck_conn)

    log.info("syncing_tables")
    total = 0
    total += await sync_search_cache(pg_conn, duck_conn)
    total += await sync_search_result_url(pg_conn, duck_conn)
    total += await sync_fetch_cache(pg_conn, duck_conn)

    log.info("sync_complete", total_rows=total)

    await pg_conn.close()
    duck_conn.close()
    log.info("done", duckdb=str(LOCAL_DUCKDB_PATH))


if __name__ == "__main__":
    asyncio.run(main())
