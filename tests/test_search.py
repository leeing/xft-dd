from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from diligence.models import SearchItem, make_item_id
from diligence.utils.minimax_search import dedup_items, run_search


def _make_item(
    url: str | None,
    title: str = "t",
    snippet: str = "s",
    query: str = "q",
    dim_id: str = "basic_info",
) -> SearchItem:
    return SearchItem(
        id=make_item_id(url=url, title=title, snippet=snippet),
        title=title,
        url=url,
        snippet=snippet,
        query=query,
        dimension_id=dim_id,
        fetched_at=datetime.now(UTC),
    )


def _make_response(organic: list[dict]) -> MagicMock:
    """Build a mock httpx.Response returning the given organic results."""
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"organic": organic, "base_resp": {"status_code": 0}}
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture()
def mock_search_success():
    organic = [
        {"title": "企业A - 企查查", "link": "https://qcc.com/1", "snippet": "注册资本100万", "date": ""},
        {"title": "企业A - 天眼查", "link": "https://tianyancha.com/1", "snippet": "法人张三", "date": ""},
    ]
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_make_response(organic))
    with patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture()
def mock_search_timeout():
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    with patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


async def test_run_search_returns_items(mock_search_success) -> None:
    items = await run_search(query="某公司 工商注册", dimension_id="basic_info", timeout=30)
    assert len(items) == 2
    assert items[0].query == "某公司 工商注册"
    assert items[0].dimension_id == "basic_info"
    assert items[0].rank == 0


async def test_run_search_timeout_raises(mock_search_timeout) -> None:
    with pytest.raises(httpx.TimeoutException):
        await run_search(query="q", dimension_id="basic_info", timeout=1)


async def test_run_search_uses_api_key() -> None:
    """Authorization header must contain the configured API key."""
    organic = [{"title": "T", "link": "https://a.com", "snippet": "s", "date": ""}]
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_make_response(organic))

    with patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        with patch("diligence.utils.minimax_search.settings") as mock_settings:
            mock_settings.minimax_api_key = "test-key-xyz"
            mock_settings.minimax_base_url = "https://api.minimaxi.chat/v1"
            await run_search(query="q", dimension_id="basic_info", timeout=30)

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Bearer test-key-xyz" in headers.get("Authorization", "")


async def test_run_search_max_results_truncated() -> None:
    """max_results parameter limits the number of returned items."""
    organic = [
        {"title": f"T{i}", "link": f"https://example.com/{i}", "snippet": f"s{i}", "date": ""} for i in range(10)
    ]
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_make_response(organic))

    with patch("diligence.utils.minimax_search.httpx.AsyncClient", return_value=mock_client):
        items = await run_search(query="q", dimension_id="basic_info", timeout=30, max_results=3)

    assert len(items) == 3


def test_dedup_by_url() -> None:
    item_a = _make_item(url="https://qcc.com/1", title="A", snippet="sa")
    item_b = _make_item(url="https://qcc.com/1", title="B", snippet="sb")
    item_c = _make_item(url="https://qcc.com/2", title="C", snippet="sc")
    result = dedup_items([item_a, item_b, item_c])
    assert len(result) == 2
    assert {i.url for i in result} == {"https://qcc.com/1", "https://qcc.com/2"}


def test_dedup_by_title_snippet_when_no_url() -> None:
    item_a = _make_item(url=None, title="企业A", snippet="注册资本100万")
    item_b = _make_item(url=None, title="企业A", snippet="注册资本100万")  # duplicate
    item_c = _make_item(url=None, title="企业A", snippet="注册资本200万")
    result = dedup_items([item_a, item_b, item_c])
    assert len(result) == 2
