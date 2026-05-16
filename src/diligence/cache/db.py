"""Async SQLAlchemy engine/session helpers for the optional cache."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from diligence.cache.models import metadata
from diligence.settings import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_tables_ready = False


def _ensure_sqlite_parent(database_url: str) -> None:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("sqlite"):
        return
    path = parsed.path
    if not path or path in (":memory:", "/:memory:"):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _normalise_asyncpg_url(database_url: str) -> tuple[str, dict[str, object]]:
    """Convert libpq-style Neon URL params into asyncpg connect args."""
    parsed = urlsplit(database_url)
    if parsed.scheme != "postgresql+asyncpg":
        return database_url, {}

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    ssl_value = query.pop("ssl", "")
    sslmode = query.pop("sslmode", "")
    query.pop("channel_binding", None)

    connect_args: dict[str, object] = {}
    if ssl_value or sslmode:
        connect_args["ssl"] = ssl_value.lower() not in ("0", "false", "disable") and sslmode != "disable"

    normalised = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return normalised, connect_args


def get_engine() -> AsyncEngine:
    """Return a process-global async SQLAlchemy engine."""
    global _engine, _sessionmaker  # noqa: PLW0603
    if _engine is None:
        database_url, connect_args = _normalise_asyncpg_url(settings.cache_database_url)
        _ensure_sqlite_parent(database_url)
        _engine = create_async_engine(database_url, connect_args=connect_args)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a process-global async sessionmaker."""
    if _sessionmaker is None:
        get_engine()
    if _sessionmaker is None:
        msg = "cache sessionmaker was not initialised"
        raise RuntimeError(msg)
    return _sessionmaker


async def ensure_tables() -> None:
    """Create cache tables when configured to do so."""
    global _tables_ready  # noqa: PLW0603
    if _tables_ready or not settings.cache_create_tables:
        return
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    _tables_ready = True


async def reset_engine_for_tests() -> None:
    """Dispose global engine/session state; used by tests that patch settings."""
    global _engine, _sessionmaker, _tables_ready  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
    _tables_ready = False
