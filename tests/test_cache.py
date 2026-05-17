from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from xft.pipeline.diligence.config import AppConfig, Dimension
from xft.cache.db import reset_engine_for_tests
from xft.cache.db import _normalise_asyncpg_url
from xft.cache.hashing import content_hash, normalize_markdown
from xft.cache.repository import FetchCacheRepo, SearchCacheKey, SearchCacheRepo
from xft.core.search_models import SearchItem, make_item_id
from xft.settings import settings
from xft.utils.fetch import enrich_items
from xft.utils.minimax_search import run_search


async def _enable_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await reset_engine_for_tests()
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "search_cache_enabled", True)
    monkeypatch.setattr(settings, "fetch_cache_enabled", True)
    monkeypatch.setattr(settings, "cache_database_url", f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    monkeypatch.setattr(settings, "cache_create_tables", True)
    monkeypatch.setattr(settings, "cache_policy_version", "test-v1")
    monkeypatch.setattr(settings, "search_cache_ttl_days", 14)
    monkeypatch.setattr(settings, "fetch_cache_ttl_days", 30)
    monkeypatch.setattr(settings, "fetch_cache_lock_minutes", 10)
    monkeypatch.setattr(settings, "cache_worker_id", "worker-a")


@pytest.fixture(autouse=True)
async def _reset_cache_engine_after_test():
    yield
    await reset_engine_for_tests()


def _response(organic: list[dict]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"organic": organic, "base_resp": {"status_code": 0}}
    resp.raise_for_status = MagicMock()
    return resp


def _item(url: str, title: str = "佛山市固特家居制品有限公司") -> SearchItem:
    return SearchItem(
        id=make_item_id(url=url, title=title, snippet="snippet"),
        title=title,
        url=url,
        snippet="snippet",
        query="q",
        dimension_id="basic_info",
        fetched_at=datetime.now(UTC),
    )


def test_content_hash_normalizes_blank_lines() -> None:
    assert normalize_markdown(" a\r\n\r\n\r\nb \n") == "a\n\nb"
    assert content_hash("a\n\n\nb") == content_hash(" a\r\n\r\nb ")


def test_normalise_asyncpg_neon_url_strips_libpq_params() -> None:
    url, connect_args = _normalise_asyncpg_url(
        "postgresql+asyncpg://u:p@example/db?channel_binding=require&sslmode=require"
    )
    assert url == "postgresql+asyncpg://u:p@example/db"
    assert connect_args == {"ssl": True}


async def test_search_cache_repo_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    key = SearchCacheKey(
        provider="minimax",
        query_text="某公司 工商注册",
        params={"endpoint": "https://api.example/v1/coding_plan/search", "max_results": 0},
    )
    organic = [{"title": "某公司", "link": "https://www.example.com/a/?utm_source=x", "snippet": "注册资本100万"}]

    await SearchCacheRepo().put_success(key, raw_response={"organic": organic, "extra": "kept"}, organic=organic)
    items = await SearchCacheRepo().get_items(key, dimension_id="basic_info")

    assert items is not None
    assert len(items) == 1
    assert items[0].url == "https://www.example.com/a/?utm_source=x"
    assert items[0].query == "某公司 工商注册"
    assert items[0].dimension_id == "basic_info"


async def test_run_search_uses_l1_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "minimax_base_url", "https://api.example/v1")
    key = SearchCacheKey(
        provider="minimax",
        query_text="q",
        params={"endpoint": "https://api.example/v1/coding_plan/search", "max_results": 0},
    )
    organic = [{"title": "T", "link": "https://example.com/a", "snippet": "S"}]
    await SearchCacheRepo().put_success(key, raw_response={"organic": organic}, organic=organic)

    with patch("xft.utils.minimax_search.httpx.AsyncClient") as mock_client_cls:
        items = await run_search(query="q", dimension_id="basic_info", timeout=30, max_results=0)

    mock_client_cls.assert_not_called()
    assert len(items) == 1
    assert items[0].title == "T"


async def test_run_search_writes_l1_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "minimax_base_url", "https://api.example/v1")
    organic = [{"title": "T", "link": "https://example.com/a", "snippet": "S"}]
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_response(organic))

    with patch("xft.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        await run_search(query="q-new", dimension_id="basic_info", timeout=30, max_results=0)

    key = SearchCacheKey(
        provider="minimax",
        query_text="q-new",
        params={"endpoint": "https://api.example/v1/coding_plan/search", "max_results": 0},
    )
    cached = await SearchCacheRepo().get_items(key, dimension_id="basic_info")
    assert cached is not None
    assert cached[0].url == "https://example.com/a"


async def test_fetch_cache_repo_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    await FetchCacheRepo().put_success("https://www.example.com/a/", "hello\n\nworld")
    hit = await FetchCacheRepo().get_markdown("https://example.com/a")
    assert hit is not None
    assert hit.markdown == "hello\n\nworld"


async def test_fetch_cache_lease_blocks_other_worker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    repo = FetchCacheRepo()
    assert await repo.acquire_lease("https://example.com/locked") is True

    monkeypatch.setattr(settings, "cache_worker_id", "worker-b")
    assert await repo.acquire_lease("https://example.com/locked") is False


async def test_enrich_items_uses_l2_fetch_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    await _enable_cache(monkeypatch, tmp_path)
    await FetchCacheRepo().put_success("https://example.com/page", "cached full text " * 20)
    items = [_item("https://example.com/page")]

    with patch("xft.utils.fetch._fetch_page_markdown", new=AsyncMock()) as mock_fetch:
        result = await enrich_items(items, blocked_domains=[], target="佛山市固特家居制品有限公司", crawler=MagicMock())

    mock_fetch.assert_not_called()
    assert result[0].full_text.startswith("cached full text")


async def test_crawler_mode_requires_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from xft.pipeline.diligence.crawler_mode import run_crawler_mode

    monkeypatch.setattr(settings, "cache_enabled", False)
    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(id="basic_info", name="工商", order=10, minimax_queries=["{target} 工商"], summary_prompt="p")
        ],
    )
    assert await run_crawler_mode("某公司", cfg, only=None, skip=None) == 1


async def test_crawler_mode_l1_hit_skips_search_and_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from xft.pipeline.diligence.crawler_mode import run_crawler_mode

    await _enable_cache(monkeypatch, tmp_path)
    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(id="basic_info", name="工商", order=10, minimax_queries=["{target} 工商"], summary_prompt="p")
        ],
    )
    query = "某公司 工商"
    key = SearchCacheKey(
        provider="minimax",
        query_text=query,
        params={"endpoint": "https://api.minimax.io/v1/coding_plan/search", "max_results": 0},
    )
    await SearchCacheRepo().put_success(key, raw_response={"organic": []}, organic=[])

    with patch("xft.pipeline.diligence.crawler_mode.run_search", new=AsyncMock()) as mock_search:
        with patch("xft.pipeline.diligence.crawler_mode.enrich_items", new=AsyncMock()) as mock_enrich:
            exit_code = await run_crawler_mode("某公司", cfg, only=None, skip=None)

    assert exit_code == 0
    mock_search.assert_not_called()
    mock_enrich.assert_not_called()


async def test_crawler_mode_l1_miss_searches_and_fetches(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from xft.pipeline.diligence.crawler_mode import run_crawler_mode_for_target

    await _enable_cache(monkeypatch, tmp_path)
    cfg = AppConfig(
        merge_prompt="x",
        dimensions=[
            Dimension(id="basic_info", name="工商", order=10, minimax_queries=["{target} 工商"], summary_prompt="p")
        ],
    )
    item = _item("https://example.com/a")
    enriched = item.model_copy(update={"full_text": "full text " * 20})

    search_mock = AsyncMock(return_value=[item])
    enrich_mock = AsyncMock(return_value=[enriched])
    with (
        patch("xft.pipeline.diligence.crawler_mode.run_search", new=search_mock) as mock_search,
        patch("xft.pipeline.diligence.crawler_mode.enrich_items", new=enrich_mock) as mock_enrich,
    ):
        stats = await run_crawler_mode_for_target("某公司", cfg)

    mock_search.assert_awaited_once()
    mock_enrich.assert_awaited_once()
    assert stats.l1_misses == 1
    assert stats.l1_hits == 0
    assert stats.full_text_items == 1
