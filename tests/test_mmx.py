from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from diligence.models import SearchItem, make_item_id
from diligence.utils.mmx import dedup_items, run_mmx_search


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


@pytest.fixture()
def mock_mmx_success():
    mmx_output = json.dumps(
        {
            "organic": [
                {"title": "企业A - 企查查", "link": "https://qcc.com/1", "snippet": "注册资本100万"},
                {"title": "企业A - 天眼查", "link": "https://tianyancha.com/1", "snippet": "法人张三"},
            ],
        }
    ).encode()

    async def fake_exec(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(mmx_output, b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        yield


@pytest.fixture()
def mock_mmx_timeout():
    async def fake_exec(*args, **kwargs):
        proc = MagicMock()

        async def slow_communicate():
            await asyncio.sleep(999)
            return (b"", b"")

        proc.communicate = slow_communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        yield


async def test_run_mmx_search_returns_items(mock_mmx_success) -> None:
    items = await run_mmx_search(query="某公司 工商注册", dimension_id="basic_info", timeout=30)
    assert len(items) == 2
    assert items[0].query == "某公司 工商注册"
    assert items[0].dimension_id == "basic_info"
    assert items[0].rank == 0


async def test_run_mmx_search_timeout_raises(mock_mmx_timeout) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await run_mmx_search(query="q", dimension_id="basic_info", timeout=1)


async def test_run_mmx_search_strips_minimax_env() -> None:
    """MINIMAX_* env vars must not be passed to subprocess."""
    import os

    captured_env: dict[str, str] = {}

    async def fake_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(json.dumps({"organic": []}).encode(), b""))
        return proc

    with patch.dict(os.environ, {"MINIMAX_API_KEY": "secret", "PATH": "/usr/bin"}):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await run_mmx_search(query="q", dimension_id="basic_info", timeout=30)

    assert "MINIMAX_API_KEY" not in captured_env
    assert "PATH" in captured_env


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
