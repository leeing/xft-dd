"""Repository layer for SQL-backed search/fetch cache."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from xft.cache.db import ensure_tables, get_sessionmaker
from xft.cache.hashing import content_hash, stable_hash, stable_json_hash
from xft.cache.models import fetch_cache, search_cache, search_result_url
from xft.core.search_models import SearchItem, make_item_id
from xft.settings import settings
from xft.utils.minimax_search import normalize_url
from xft.utils.source_registry import classify_source


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _search_params_hash(params: dict[str, Any]) -> str:
    return stable_json_hash(params)


@dataclass(frozen=True)
class SearchCacheKey:
    provider: str
    query_text: str
    params: dict[str, Any]

    @property
    def query_hash(self) -> str:
        return stable_hash(self.query_text)

    @property
    def params_hash(self) -> str:
        return _search_params_hash(self.params)


class SearchCacheRepo:
    """Read/write MiniMax search responses and indexed result URLs."""

    async def get_cached_query_hashes(
        self,
        query_texts: list[str],
        *,
        params: dict[str, Any],
        provider: str = "minimax",
    ) -> set[str]:
        """Batch fetch which query texts have a valid L1 cache entry.

        Returns a set of query_hashes that exist in cache with status='success'.
        """
        if not query_texts:
            return set()
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        params_hash = _search_params_hash(params)
        async with sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(search_cache.c.query_hash, search_cache.c.expires_at)
                        .where(search_cache.c.provider == provider)
                        .where(search_cache.c.query_hash.in_([stable_hash(q) for q in query_texts]))
                        .where(search_cache.c.params_hash == params_hash)
                        .where(search_cache.c.policy_version == settings.cache_policy_version)
                        .where(search_cache.c.status == "success")
                    )
                )
                .mappings()
                .all()
            )
        hit_hashes: set[str] = set()
        for row in rows:
            expires_at = _as_aware(row["expires_at"])
            if expires_at is None or expires_at > now:
                hit_hashes.add(row["query_hash"])
        return hit_hashes

    async def get_items(self, key: SearchCacheKey, *, dimension_id: str) -> list[SearchItem] | None:
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        async with sessionmaker() as session:
            cache_row = (
                (
                    await session.execute(
                        select(search_cache)
                        .where(search_cache.c.provider == key.provider)
                        .where(search_cache.c.query_hash == key.query_hash)
                        .where(search_cache.c.params_hash == key.params_hash)
                        .where(search_cache.c.policy_version == settings.cache_policy_version)
                        .where(search_cache.c.status == "success")
                    )
                )
                .mappings()
                .first()
            )
            if cache_row is None:
                return None
            expires_at = _as_aware(cache_row["expires_at"])
            if expires_at is not None and expires_at <= now:
                return None

            rows = (
                (
                    await session.execute(
                        select(search_result_url)
                        .where(search_result_url.c.search_cache_id == cache_row["id"])
                        .order_by(search_result_url.c.rank, search_result_url.c.id)
                    )
                )
                .mappings()
                .all()
            )

        fetched_at = _as_aware(cache_row["created_at"]) or now
        items: list[SearchItem] = []
        for row in rows:
            original_url = row["original_url"]
            title = row["title"]
            snippet = row["snippet"]
            items.append(
                SearchItem(
                    id=make_item_id(url=original_url, title=title, snippet=snippet),
                    title=title,
                    url=original_url,
                    snippet=snippet,
                    query=key.query_text,
                    dimension_id=dimension_id,
                    source="minimax",
                    rank=row["rank"],
                    fetched_at=fetched_at,
                )
            )
        return items

    async def put_success(
        self,
        key: SearchCacheKey,
        *,
        raw_response: dict[str, Any],
        organic: list[dict[str, Any]],
    ) -> None:
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        expires_at = now + timedelta(days=settings.search_cache_ttl_days)
        async with sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(search_cache.c.id)
                    .where(search_cache.c.provider == key.provider)
                    .where(search_cache.c.query_hash == key.query_hash)
                    .where(search_cache.c.params_hash == key.params_hash)
                    .where(search_cache.c.policy_version == settings.cache_policy_version)
                )
            ).scalar_one_or_none()
            if existing is not None:
                await session.execute(delete(search_result_url).where(search_result_url.c.search_cache_id == existing))
                await session.execute(delete(search_cache).where(search_cache.c.id == existing))

            cache_id = (
                await session.execute(
                    insert(search_cache)
                    .values(
                        provider=key.provider,
                        query_text=key.query_text,
                        query_hash=key.query_hash,
                        params_hash=key.params_hash,
                        policy_version=settings.cache_policy_version,
                        status="success",
                        raw_response_json=_json_dumps(raw_response),
                        result_count=len(organic),
                        error=None,
                        created_at=now,
                        expires_at=expires_at,
                    )
                    .returning(search_cache.c.id)
                )
            ).scalar_one()
            rows = [
                {
                    "search_cache_id": cache_id,
                    "normalized_url": normalize_url(entry.get("link") or None),
                    "original_url": entry.get("link") or None,
                    "title": entry.get("title", ""),
                    "snippet": entry.get("snippet", ""),
                    "rank": rank,
                    "raw_item_json": _json_dumps(entry),
                    "created_at": now,
                }
                for rank, entry in enumerate(organic)
            ]
            if rows:
                await session.execute(insert(search_result_url), rows)


@dataclass(frozen=True)
class FetchCacheHit:
    markdown: str


class FetchCacheRepo:
    """Read/write crawl4ai markdown by normalized URL."""

    async def get_markdown(self, url: str) -> FetchCacheHit | None:
        norm = normalize_url(url)
        if norm is None:
            return None
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        async with sessionmaker() as session:
            row = (
                (
                    await session.execute(
                        select(fetch_cache)
                        .where(fetch_cache.c.normalized_url == norm)
                        .where(fetch_cache.c.policy_version == settings.cache_policy_version)
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["status"] != "success" or not row["markdown"]:
            return None
        expires_at = _as_aware(row["expires_at"])
        if expires_at is not None and expires_at <= now:
            return None
        return FetchCacheHit(markdown=row["markdown"])

    async def acquire_lease(self, url: str) -> bool:
        """Try to reserve a URL for crawl; return False when another worker owns it.

        Failed rows observe their retry cooldown via expires_at.  The lease is
        intentionally best-effort: success/failure writes clear the lock.
        """
        norm = normalize_url(url)
        if norm is None:
            return False
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        locked_until = now + timedelta(minutes=settings.fetch_cache_lock_minutes)
        src = classify_source(url)
        async with sessionmaker() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(fetch_cache)
                        .where(fetch_cache.c.normalized_url == norm)
                        .where(fetch_cache.c.policy_version == settings.cache_policy_version)
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                expires_at = _as_aware(row["expires_at"])
                if row["status"] == "failed" and expires_at is not None and expires_at > now:
                    return False
                row_locked_until = _as_aware(row["locked_until"])
                if (
                    row_locked_until is not None
                    and row_locked_until > now
                    and row["locked_by"] != settings.cache_worker_id
                ):
                    return False
                await session.execute(
                    update(fetch_cache)
                    .where(fetch_cache.c.normalized_url == norm)
                    .values(
                        status="pending",
                        locked_by=settings.cache_worker_id,
                        locked_until=locked_until,
                        policy_version=settings.cache_policy_version,
                        updated_at=now,
                    )
                )
                return True

            values = {
                "normalized_url": norm,
                "original_url": url,
                "final_url": url,
                "source_type": src.source_type,
                "authority_level": src.authority_level,
                "should_fetch_bias": src.should_fetch_bias,
                "status": "pending",
                "markdown": None,
                "content_hash": None,
                "error": None,
                "fetched_at": None,
                "expires_at": None,
                "retry_count": 0,
                "locked_by": settings.cache_worker_id,
                "locked_until": locked_until,
                "policy_version": settings.cache_policy_version,
                "updated_at": now,
            }
            try:
                await session.execute(insert(fetch_cache).values(**values))
            except IntegrityError:
                return False
            return True

    async def put_success(self, url: str, markdown: str) -> None:
        norm = normalize_url(url)
        if norm is None:
            return
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        src = classify_source(url)
        values = {
            "normalized_url": norm,
            "original_url": url,
            "final_url": url,
            "source_type": src.source_type,
            "authority_level": src.authority_level,
            "should_fetch_bias": src.should_fetch_bias,
            "status": "success",
            "markdown": markdown,
            "content_hash": content_hash(markdown),
            "error": None,
            "fetched_at": now,
            "expires_at": now + timedelta(days=settings.fetch_cache_ttl_days),
            "retry_count": 0,
            "locked_by": None,
            "locked_until": None,
            "policy_version": settings.cache_policy_version,
            "updated_at": now,
        }
        async with sessionmaker() as session, session.begin():
            existing = (
                await session.execute(select(fetch_cache.c.normalized_url).where(fetch_cache.c.normalized_url == norm))
            ).scalar_one_or_none()
            if existing is None:
                await session.execute(insert(fetch_cache).values(**values))
            else:
                await session.execute(update(fetch_cache).where(fetch_cache.c.normalized_url == norm).values(**values))

    async def put_failed(self, url: str, error: str) -> None:
        norm = normalize_url(url)
        if norm is None:
            return
        await ensure_tables()
        sessionmaker = get_sessionmaker()
        now = _now()
        src = classify_source(url)
        async with sessionmaker() as session, session.begin():
            row = (
                (await session.execute(select(fetch_cache.c.retry_count).where(fetch_cache.c.normalized_url == norm)))
                .mappings()
                .first()
            )
            retry_count = int(row["retry_count"]) + 1 if row is not None else 1
            values = {
                "normalized_url": norm,
                "original_url": url,
                "final_url": url,
                "source_type": src.source_type,
                "authority_level": src.authority_level,
                "should_fetch_bias": src.should_fetch_bias,
                "status": "failed",
                "markdown": None,
                "content_hash": None,
                "error": error,
                "fetched_at": now,
                "expires_at": now + timedelta(hours=settings.fetch_failed_retry_hours),
                "retry_count": retry_count,
                "locked_by": None,
                "locked_until": None,
                "policy_version": settings.cache_policy_version,
                "updated_at": now,
            }
            if row is None:
                await session.execute(insert(fetch_cache).values(**values))
            else:
                await session.execute(update(fetch_cache).where(fetch_cache.c.normalized_url == norm).values(**values))
