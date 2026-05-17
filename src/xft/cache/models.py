"""SQLAlchemy table metadata for the optional cache.

The project intentionally keeps table definitions in one place so Alembic can
later use this metadata as its migration source of truth.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text, UniqueConstraint

metadata = MetaData()

search_cache = Table(
    "search_cache",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String(32), nullable=False),
    Column("query_text", Text, nullable=False),
    Column("query_hash", String(64), nullable=False),
    Column("params_hash", String(64), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("raw_response_json", Text, nullable=False),
    Column("result_count", Integer, nullable=False),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    UniqueConstraint("provider", "query_hash", "params_hash", "policy_version", name="uq_search_cache_key"),
)

search_result_url = Table(
    "search_result_url",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("search_cache_id", Integer, ForeignKey("search_cache.id", ondelete="CASCADE"), nullable=False),
    Column("normalized_url", Text),
    Column("original_url", Text),
    Column("title", Text, nullable=False),
    Column("snippet", Text, nullable=False),
    Column("rank", Integer),
    Column("raw_item_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

fetch_cache = Table(
    "fetch_cache",
    metadata,
    Column("normalized_url", Text, primary_key=True),
    Column("original_url", Text),
    Column("final_url", Text),
    Column("source_type", String(64)),
    Column("authority_level", String(32)),
    Column("should_fetch_bias", String(32)),
    Column("status", String(32), nullable=False),
    Column("markdown", Text),
    Column("content_hash", String(64)),
    Column("error", Text),
    Column("fetched_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("locked_by", String(128)),
    Column("locked_until", DateTime(timezone=True)),
    Column("policy_version", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Index("ix_search_cache_lookup", search_cache.c.provider, search_cache.c.query_hash, search_cache.c.params_hash)
Index("ix_search_result_url_normalized", search_result_url.c.normalized_url)
Index("ix_fetch_cache_status", fetch_cache.c.status)
