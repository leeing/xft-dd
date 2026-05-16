"""Sync cache data from remote PostgreSQL to local DuckDB.

Usage:
    uv run python sync_to_duckdb.py
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import dotenv
import duckdb

# Load .env file
dotenv.load_dotenv()


# PostgreSQL connection from .env
PG_URL = os.getenv("CACHE_DATABASE_URL", "")
if "postgresql" not in PG_URL:
    print("ERROR: CACHE_DATABASE_URL does not point to PostgreSQL")
    print(f"Current: {PG_URL}")
    sys.exit(1)

# Parse PG URL
# Format: postgresql+asyncpg://user:password@host:port/dbname?ssl=true
pg_url = PG_URL.replace("postgresql+asyncpg://", "").split("@")
if len(pg_url) != 2:
    print(f"ERROR: Cannot parse PostgreSQL URL: {PG_URL}")
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


async def sync_search_cache(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync search_cache table."""
    rows = await pg_conn.fetch("SELECT * FROM search_cache")
    if not rows:
        print("  search_cache: 0 rows (skip)")
        return 0

    columns = [desc[0] for desc in await pg_conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'search_cache'")]
    data = []
    for row in rows:
        # Convert timezone-aware timestamps to naive for DuckDB
        record = []
        for col in columns:
            val = row[col]
            if hasattr(val, 'tzinfo') and val.tzinfo is not None:
                val = val.replace(tzinfo=None)
            record.append(val)
        data.append(record)

    duck_conn.executemany(
        f"INSERT INTO search_cache ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
        data
    )
    count = len(data)
    print(f"  search_cache: {count} rows")
    return count


async def sync_search_result_url(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync search_result_url table."""
    rows = await pg_conn.fetch("SELECT * FROM search_result_url")
    if not rows:
        print("  search_result_url: 0 rows (skip)")
        return 0

    columns = [desc[0] for desc in await pg_conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'search_result_url'")]
    data = []
    for row in rows:
        record = []
        for col in columns:
            val = row[col]
            if hasattr(val, 'tzinfo') and val.tzinfo is not None:
                val = val.replace(tzinfo=None)
            record.append(val)
        data.append(record)

    duck_conn.executemany(
        f"INSERT INTO search_result_url ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
        data
    )
    count = len(data)
    print(f"  search_result_url: {count} rows")
    return count


async def sync_fetch_cache(pg_conn: asyncpg.Connection, duck_conn: duckdb.DuckDBPyConnection) -> int:
    """Sync fetch_cache table."""
    rows = await pg_conn.fetch("SELECT * FROM fetch_cache")
    if not rows:
        print("  fetch_cache: 0 rows (skip)")
        return 0

    columns = [desc[0] for desc in await pg_conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'fetch_cache'")]
    data = []
    for row in rows:
        record = []
        for col in columns:
            val = row[col]
            if hasattr(val, 'tzinfo') and val.tzinfo is not None:
                val = val.replace(tzinfo=None)
            record.append(val)
        data.append(record)

    duck_conn.executemany(
        f"INSERT INTO fetch_cache ({', '.join(columns)}) VALUES ({', '.join(['?' for _ in columns])})",
        data
    )
    count = len(data)
    print(f"  fetch_cache: {count} rows")
    return count


async def main() -> None:
    print(f"Connecting to PostgreSQL: {host_port}/{db_name}")
    print(f"Target DuckDB: {LOCAL_DUCKDB_PATH}")

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

    print("\nSyncing tables...")
    total = 0
    total += await sync_search_cache(pg_conn, duck_conn)
    total += await sync_search_result_url(pg_conn, duck_conn)
    total += await sync_fetch_cache(pg_conn, duck_conn)

    print(f"\nTotal rows synced: {total}")

    await pg_conn.close()
    duck_conn.close()
    print(f"\nDone! Local DuckDB: {LOCAL_DUCKDB_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
