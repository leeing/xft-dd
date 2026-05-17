"""Cache index helpers for data/web run directories."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from xft.web.models import (
    WebEvidenceRecord,
    WebFetchConfig,
    WebPageRecord,
    WebProviderConfig,
    WebSearchQueryRecord,
    WebSearchResultRecord,
)


@dataclass(frozen=True)
class ExistingWebRun:
    """A reusable Web enrichment run discovered on disk."""

    run_dir: Path
    queries: int
    results: int
    evidence: int


def stable_hash(value: Any) -> str:
    """Return a stable short hash for JSON-like values."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def file_hash(path: str | Path | None) -> str:
    """Return a content hash for an optional file path."""
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return hashlib.sha1(file_path.read_bytes(), usedforsecurity=False).hexdigest()


class SearchCacheKey(BaseModel):
    """Stable key for one provider query cache entry."""

    credit_code: str | None = None
    company_name: str
    dimension_id: str
    query: str
    provider: str
    provider_params_hash: str = ""
    max_results: int = 0
    cache_policy_version: str = "v1"

    @property
    def key_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class FetchCacheKey(BaseModel):
    """Stable key for one fetched page cache entry."""

    credit_code: str | None = None
    company_name: str
    provider: str
    url: str | None = None
    title: str = ""
    fetch_config_hash: str = ""
    cache_policy_version: str = "v1"

    @property
    def key_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class ExtractionCacheKey(BaseModel):
    """Stable key for one dimension evidence extraction entry."""

    credit_code: str | None = None
    company_name: str
    dimension_id: str
    result_fingerprint: str
    extract_prompt_version: str = ""
    extract_prompt_hash: str = ""
    extract_model: str = ""
    extract_config_hash: str = ""
    cache_policy_version: str = "v1"

    @property
    def key_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


# Backward-compatible import name used by the first cache implementation.
WebCacheKey = SearchCacheKey


@dataclass(frozen=True)
class CachedQuery:
    """A cached provider query and its normalized search results."""

    query: WebSearchQueryRecord
    results: list[WebSearchResultRecord]
    source_run_dir: Path


@dataclass(frozen=True)
class CachedExtraction:
    """Cached evidence extracted for one dimension."""

    dimension_id: str
    evidence: list[WebEvidenceRecord]
    extraction_request: dict[str, Any]
    extraction_result: dict[str, Any]
    source_run_dir: Path
    key_hash: str


@dataclass(frozen=True)
class CacheRuntimeConfig:
    """Normalized config fingerprints used to build cache keys."""

    credit_code: str | None
    company_name: str
    cache_policy_version: str
    provider_params_hashes: dict[str, str]
    max_results_per_query: int
    fetch_config_hash: str
    extract_prompt_version: str
    extract_prompt_hash: str
    extract_model: str
    extract_config_hash: str


def find_existing_run(company_dir: Path) -> ExistingWebRun | None:
    """Return the latest complete-ish Web run for a company directory."""
    if not company_dir.exists():
        return None
    runs = [
        path
        for path in company_dir.iterdir()
        if path.is_dir() and (path / "manifest.json").exists() and (path / "web_evidence.jsonl").exists()
    ]
    if not runs:
        return None
    run_dir = sorted(runs)[-1]
    return ExistingWebRun(
        run_dir=run_dir,
        queries=count_jsonl_rows(run_dir / "queries.jsonl"),
        results=count_jsonl_rows(run_dir / "search_results.jsonl"),
        evidence=count_jsonl_rows(run_dir / "web_evidence.jsonl"),
    )


def find_run_by_id(company_dir: Path, run_id: str) -> Path | None:
    """Find a specific run directory by id."""
    path = company_dir / run_id
    if path.is_dir() and (path / "manifest.json").exists():
        return path
    return None


def load_cached_queries(
    run_dir: Path,
    *,
    runtime: CacheRuntimeConfig | None = None,
) -> dict[str | tuple[str, str, str], CachedQuery]:
    """Load cached query records keyed by SearchCacheKey hash.

    Without runtime config, this returns the legacy (dimension_id, provider, query)
    keys so older call sites and cache indexes remain usable.
    """
    queries = [WebSearchQueryRecord.model_validate(row) for row in _read_jsonl(run_dir / "queries.jsonl")]
    results = [WebSearchResultRecord.model_validate(row) for row in _read_jsonl(run_dir / "search_results.jsonl")]
    by_query_id: dict[str, list[WebSearchResultRecord]] = {}
    for result in results:
        by_query_id.setdefault(result.query_id, []).append(result)
    cached: dict[str | tuple[str, str, str], CachedQuery] = {}
    for query in queries:
        if query.status == "success" and by_query_id.get(query.query_id):
            key: str | tuple[str, str, str]
            if runtime is None:
                key = (query.dimension_id, query.provider, query.query)
            else:
                key = query.cache_key or make_search_cache_key(
                    credit_code=runtime.credit_code,
                    company_name=runtime.company_name,
                    dimension_id=query.dimension_id,
                    query=query.query,
                    provider=query.provider,
                    provider_params_hash=runtime.provider_params_hashes.get(query.provider, ""),
                    max_results=runtime.max_results_per_query,
                    cache_policy_version=runtime.cache_policy_version,
                ).key_hash
            cached[key] = CachedQuery(
                query=query,
                results=by_query_id[query.query_id],
                source_run_dir=run_dir,
            )
    return cached


def load_cached_extractions(
    run_dir: Path,
    *,
    runtime: CacheRuntimeConfig,
) -> dict[str, CachedExtraction]:
    """Load reusable extraction evidence keyed by ExtractionCacheKey hash."""
    results = [WebSearchResultRecord.model_validate(row) for row in _read_jsonl(run_dir / "search_results.jsonl")]
    evidence = [WebEvidenceRecord.model_validate(row) for row in _read_jsonl(run_dir / "web_evidence.jsonl")]
    requests = _payloads_by_dimension(_read_jsonl(run_dir / "extraction_requests.jsonl"))
    extraction_results = _payloads_by_dimension(_read_jsonl(run_dir / "extraction_results.jsonl"))
    results_by_dimension: dict[str, list[WebSearchResultRecord]] = {}
    evidence_by_dimension: dict[str, list[WebEvidenceRecord]] = {}
    for result in results:
        results_by_dimension.setdefault(result.dimension_id, []).append(result)
    for item in evidence:
        evidence_by_dimension.setdefault(item.dimension_id, []).append(item)
    cached: dict[str, CachedExtraction] = {}
    for dimension_id, dimension_results in results_by_dimension.items():
        items = evidence_by_dimension.get(dimension_id, [])
        if not items:
            continue
        key = make_extraction_cache_key(
            credit_code=runtime.credit_code,
            company_name=runtime.company_name,
            dimension_id=dimension_id,
            results=dimension_results,
            extract_prompt_version=runtime.extract_prompt_version,
            extract_prompt_hash=runtime.extract_prompt_hash,
            extract_model=runtime.extract_model,
            extract_config_hash=runtime.extract_config_hash,
            cache_policy_version=runtime.cache_policy_version,
        )
        stored_key = next((item.extraction_cache_key for item in items if item.extraction_cache_key), None)
        cached[stored_key or key.key_hash] = CachedExtraction(
            dimension_id=dimension_id,
            evidence=items,
            extraction_request=requests.get(dimension_id, {}),
            extraction_result=extraction_results.get(dimension_id, {}),
            source_run_dir=run_dir,
            key_hash=stored_key or key.key_hash,
        )
    return cached


def write_cache_index(
    company_dir: Path,
    *,
    credit_code: str | None,
    company_name: str,
    runtime: CacheRuntimeConfig | None = None,
) -> None:
    """Write a lightweight company-level cache index for all web runs."""
    runs: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    extractions: list[dict[str, Any]] = []
    if company_dir.exists():
        for run_dir in sorted(path for path in company_dir.iterdir() if path.is_dir()):
            if not (run_dir / "manifest.json").exists():
                continue
            run_queries = [WebSearchQueryRecord.model_validate(row) for row in _read_jsonl(run_dir / "queries.jsonl")]
            run_results = [
                WebSearchResultRecord.model_validate(row)
                for row in _read_jsonl(run_dir / "search_results.jsonl")
            ]
            result_count_by_query: dict[str, int] = {}
            for result in run_results:
                result_count_by_query[result.query_id] = result_count_by_query.get(result.query_id, 0) + 1
            run_pages = [WebPageRecord.model_validate(row) for row in _read_jsonl(run_dir / "fetched_pages.jsonl")]
            run_evidence = [
                WebEvidenceRecord.model_validate(row)
                for row in _read_jsonl(run_dir / "web_evidence.jsonl")
            ]
            results_by_dimension: dict[str, list[WebSearchResultRecord]] = {}
            evidence_by_dimension: dict[str, int] = {}
            for result in run_results:
                results_by_dimension.setdefault(result.dimension_id, []).append(result)
            for evidence in run_evidence:
                evidence_by_dimension[evidence.dimension_id] = evidence_by_dimension.get(evidence.dimension_id, 0) + 1
            runs.append(
                {
                    "web_run_id": run_dir.name,
                    "run_dir": str(run_dir),
                    "queries": len(run_queries),
                    "results": len(run_results),
                    "pages": len(run_pages),
                    "evidence": len(run_evidence),
                }
            )
            for query in run_queries:
                search_key = make_search_cache_key(
                    credit_code=credit_code,
                    company_name=company_name,
                    dimension_id=query.dimension_id,
                    query=query.query,
                    provider=query.provider,
                    provider_params_hash=(
                        runtime.provider_params_hashes.get(query.provider, "") if runtime else ""
                    ),
                    max_results=runtime.max_results_per_query if runtime else 0,
                    cache_policy_version=runtime.cache_policy_version if runtime else "v1",
                )
                queries.append(
                    {
                        "key_hash": query.cache_key or search_key.key_hash,
                        "web_run_id": run_dir.name,
                        "query_id": query.query_id,
                        "dimension_id": query.dimension_id,
                        "provider": query.provider,
                        "query": query.query,
                        "status": query.status,
                        "result_count": result_count_by_query.get(query.query_id, 0),
                        "created_at": query.created_at.isoformat(),
                        "cache_policy_version": query.cache_policy_version,
                        "provider_params_hash": query.provider_params_hash,
                        "max_results": query.max_results,
                    }
                )
            for page in run_pages:
                page_result: WebSearchResultRecord | None = next(
                    (item for item in run_results if item.result_id == page.result_id),
                    None,
                )
                fetch_key = make_fetch_cache_key(
                    credit_code=credit_code,
                    company_name=company_name,
                    provider=page_result.provider if page_result else "",
                    url=page.url,
                    title=page.title,
                    fetch_config_hash=runtime.fetch_config_hash if runtime else "",
                    cache_policy_version=runtime.cache_policy_version if runtime else "v1",
                )
                pages.append(
                    {
                        "key_hash": fetch_key.key_hash,
                        "web_run_id": run_dir.name,
                        "page_id": page.page_id,
                        "result_id": page.result_id,
                        "url": page.url,
                        "status": page.status,
                        "content_hash": page.content_hash,
                        "page_path": page.page_path,
                        "text_length": page.text_length,
                        "created_at": page.created_at.isoformat(),
                    }
                )
            for dimension_id, dimension_results in results_by_dimension.items():
                extraction_key = make_extraction_cache_key(
                    credit_code=credit_code,
                    company_name=company_name,
                    dimension_id=dimension_id,
                    results=dimension_results,
                    extract_prompt_version=runtime.extract_prompt_version if runtime else "",
                    extract_prompt_hash=runtime.extract_prompt_hash if runtime else "",
                    extract_model=runtime.extract_model if runtime else "",
                    extract_config_hash=runtime.extract_config_hash if runtime else "",
                    cache_policy_version=runtime.cache_policy_version if runtime else "v1",
                )
                extractions.append(
                    {
                        "key_hash": (
                            next(
                                (
                                    item.extraction_cache_key
                                    for item in run_evidence
                                    if item.dimension_id == dimension_id and item.extraction_cache_key
                                ),
                                None,
                            )
                            or extraction_key.key_hash
                        ),
                        "web_run_id": run_dir.name,
                        "dimension_id": dimension_id,
                        "result_count": len(dimension_results),
                        "evidence_count": evidence_by_dimension.get(dimension_id, 0),
                        "extract_prompt_version": runtime.extract_prompt_version if runtime else "",
                        "extract_prompt_hash": runtime.extract_prompt_hash if runtime else "",
                        "extract_model": runtime.extract_model if runtime else "",
                    }
                )
    payload = {
        "schema_version": "1.1",
        "credit_code": credit_code,
        "company_name": company_name,
        "runs": runs,
        "queries": queries,
        "pages": pages,
        "extractions": extractions,
    }
    company_dir.mkdir(parents=True, exist_ok=True)
    (company_dir / "cache_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def make_runtime_config(  # noqa: PLR0913
    *,
    credit_code: str | None,
    company_name: str,
    cache_policy_version: str,
    provider_configs: dict[str, WebProviderConfig],
    max_results_per_query: int,
    fetch_config: WebFetchConfig,
    extract_prompt_version: str,
    extract_prompt_file: str,
    extract_model: str,
    extract_config: Any,
) -> CacheRuntimeConfig:
    """Build the runtime config fingerprints used by cache keys."""
    return CacheRuntimeConfig(
        credit_code=credit_code,
        company_name=company_name,
        cache_policy_version=cache_policy_version,
        provider_params_hashes={
            name: stable_hash(config.model_dump(mode="json"))
            for name, config in provider_configs.items()
        },
        max_results_per_query=max_results_per_query,
        fetch_config_hash=stable_hash(fetch_config.model_dump(mode="json")),
        extract_prompt_version=extract_prompt_version,
        extract_prompt_hash=file_hash(extract_prompt_file),
        extract_model=extract_model,
        extract_config_hash=stable_hash(extract_config),
    )


def make_search_cache_key(  # noqa: PLR0913
    *,
    credit_code: str | None,
    company_name: str,
    dimension_id: str,
    query: str,
    provider: str,
    provider_params_hash: str,
    max_results: int,
    cache_policy_version: str,
) -> SearchCacheKey:
    """Build a stable key for provider search reuse."""
    return SearchCacheKey(
        credit_code=credit_code,
        company_name=company_name,
        dimension_id=dimension_id,
        query=query.strip(),
        provider=provider,
        provider_params_hash=provider_params_hash,
        max_results=max_results,
        cache_policy_version=cache_policy_version,
    )


def make_fetch_cache_key(  # noqa: PLR0913
    *,
    credit_code: str | None,
    company_name: str,
    provider: str,
    url: str | None,
    title: str,
    fetch_config_hash: str,
    cache_policy_version: str,
) -> FetchCacheKey:
    """Build a stable key for fetched page reuse."""
    return FetchCacheKey(
        credit_code=credit_code,
        company_name=company_name,
        provider=provider,
        url=url,
        title=title,
        fetch_config_hash=fetch_config_hash,
        cache_policy_version=cache_policy_version,
    )


def make_extraction_cache_key(  # noqa: PLR0913
    *,
    credit_code: str | None,
    company_name: str,
    dimension_id: str,
    results: list[WebSearchResultRecord],
    extract_prompt_version: str,
    extract_prompt_hash: str,
    extract_model: str,
    extract_config_hash: str,
    cache_policy_version: str,
) -> ExtractionCacheKey:
    """Build a stable key for dimension-level evidence extraction reuse."""
    return ExtractionCacheKey(
        credit_code=credit_code,
        company_name=company_name,
        dimension_id=dimension_id,
        result_fingerprint=_result_fingerprint(results),
        extract_prompt_version=extract_prompt_version,
        extract_prompt_hash=extract_prompt_hash,
        extract_model=extract_model,
        extract_config_hash=extract_config_hash,
        cache_policy_version=cache_policy_version,
    )


def copy_cached_page_artifacts(
    *,
    source_run_dir: Path,
    target_run_dir: Path,
    records: list[WebSearchResultRecord],
) -> list[WebPageRecord]:
    """Copy fetched page files referenced by reused search results into a new run."""
    result_ids = {record.result_id for record in records}
    cached_pages = [
        WebPageRecord.model_validate(row)
        for row in _read_jsonl(source_run_dir / "fetched_pages.jsonl")
        if row.get("result_id") in result_ids
    ]
    for record in records:
        for relative_path in (record.page_path,):
            if not relative_path:
                continue
            source = source_run_dir / relative_path
            target = target_run_dir / relative_path
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        metadata_path = _metadata_path(record.page_path)
        if metadata_path:
            source = source_run_dir / metadata_path
            target = target_run_dir / metadata_path
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    return cached_pages


def count_jsonl_rows(path: Path) -> int:
    """Count non-empty JSONL rows without parsing the file."""
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _payloads_by_dimension(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for row in rows:
        dimension_id = row.get("dimension_id")
        if isinstance(dimension_id, str):
            payload = row.get("payload")
            payloads[dimension_id] = payload if isinstance(payload, dict) else row
    return payloads


def _result_fingerprint(results: list[WebSearchResultRecord]) -> str:
    payload = [
        {
            "provider": item.provider,
            "url": item.url,
            "title": item.title,
            "snippet": item.snippet,
            "full_text_preview": item.full_text_preview,
            "content_hash": item.content_hash,
        }
        for item in sorted(results, key=lambda item: (item.provider, item.query_id, item.rank or 0, item.url or ""))
    ]
    return stable_hash(payload)


def _metadata_path(page_path: str | None) -> str | None:
    if not page_path:
        return None
    path = Path(page_path)
    return str(path.with_suffix(".json"))
