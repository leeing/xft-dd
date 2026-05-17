"""Search provider adapters reusing existing MiniMax and Metaso clients."""

from __future__ import annotations

from typing import Protocol

import httpx

from xft.core.search_models import SearchItem
from xft.settings import settings
from xft.utils.metaso import fetch_metaso_items, fetch_metaso_search_items
from xft.utils.minimax_search import run_search
from xft.web.models import ProviderSearchResponse, RecordStatus, WebProviderConfig


class SearchProvider(Protocol):
    """Common provider interface."""

    name: str

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        """Run a Web search query."""


class MiniMaxSearchProvider:
    """MiniMax Search provider adapter."""

    def __init__(self, name: str, config: WebProviderConfig):
        self.name = name
        self.config = config

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        if not settings.minimax_api_key:
            return ProviderSearchResponse(
                provider=self.name,
                provider_type="minimax",
                query=query,
                dimension_id=dimension_id,
                status="failed",
                error="missing minimax_api_key",
            )
        try:
            items = await run_search(
                query=query,
                dimension_id=dimension_id,
                timeout=self.config.timeout_seconds,
                max_results=self.config.max_results,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return ProviderSearchResponse(
                provider=self.name,
                provider_type="minimax",
                query=query,
                dimension_id=dimension_id,
                status="failed",
                error=str(exc),
            )
        return ProviderSearchResponse(
            provider=self.name,
            provider_type="minimax",
            query=query,
            dimension_id=dimension_id,
            status="success" if items else "partial",
            items=[item.model_dump() for item in items],
        )


class MetasoSearchProvider:
    """Metaso search/chat provider adapter."""

    def __init__(self, name: str, config: WebProviderConfig):
        self.name = name
        self.config = config

    async def search(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        if not settings.metaso_api_key:
            return ProviderSearchResponse(
                provider=self.name,
                provider_type="metaso",
                mode=self.config.mode or "search",
                query=query,
                dimension_id=dimension_id,
                status="failed",
                error="missing metaso_api_key",
            )
        if self.config.mode == "chat":
            return await self._search_chat(query, dimension_id=dimension_id)
        return await self._search_web(query, dimension_id=dimension_id)

    async def _search_web(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        try:
            items, success, failed, credits = await fetch_metaso_search_items(
                dimension_id=dimension_id,
                queries=[query],
                api_key=settings.metaso_api_key,
                size=self.config.search_size,
                timeout=self.config.timeout_seconds,
                verify_tls=settings.metaso_verify_tls,
            )
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return self._failed(query, dimension_id, str(exc), mode="search")
        return self._response(
            query=query,
            dimension_id=dimension_id,
            items=items,
            success=success,
            failed=failed,
            credits=credits,
            mode="search",
        )

    async def _search_chat(self, query: str, *, dimension_id: str) -> ProviderSearchResponse:
        try:
            answer_items, source_items, success, failed, credits = await fetch_metaso_items(
                dimension_id=dimension_id,
                queries=[query],
                api_key=settings.metaso_api_key,
                timeout=self.config.timeout_seconds,
                verify_tls=settings.metaso_verify_tls,
            )
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return self._failed(query, dimension_id, str(exc), mode="chat")
        return self._response(
            query=query,
            dimension_id=dimension_id,
            items=[*source_items, *answer_items],
            success=success,
            failed=failed,
            credits=credits,
            mode="chat",
        )

    def _response(  # noqa: PLR0913
        self,
        *,
        query: str,
        dimension_id: str,
        items: list[SearchItem],
        success: int,
        failed: int,
        credits: int,
        mode: str,
    ) -> ProviderSearchResponse:
        status: RecordStatus = "success" if success else "failed" if failed else "partial"
        return ProviderSearchResponse(
            provider=self.name,
            provider_type="metaso",
            mode=mode,
            query=query,
            dimension_id=dimension_id,
            status=status,
            items=[item.model_dump() for item in items],
            credits=credits,
            error=f"{failed} metaso query failed" if failed else None,
        )

    def _failed(self, query: str, dimension_id: str, error: str, *, mode: str) -> ProviderSearchResponse:
        return ProviderSearchResponse(
            provider=self.name,
            provider_type="metaso",
            mode=mode,
            query=query,
            dimension_id=dimension_id,
            status="failed",
            error=error,
        )


def build_provider(name: str, config: WebProviderConfig) -> SearchProvider:
    if config.type == "minimax":
        return MiniMaxSearchProvider(name, config)
    if config.type == "metaso":
        return MetasoSearchProvider(name, config)
    msg = f"unsupported provider type: {config.type}"
    raise ValueError(msg)
