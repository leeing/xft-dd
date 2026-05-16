"""Write Web enrichment artifacts to data/web."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from diligence.recommender.web.models import (
    WebEvidenceRecord,
    WebPageRecord,
    WebRunManifest,
    WebSearchQueryRecord,
    WebSearchResultRecord,
    WebSkippedQueryRecord,
)


def safe_dir_name(credit_code: str | None, company_name: str) -> str:
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in company_name).strip("_") or "company"
    return f"{credit_code}_{safe_name}" if credit_code else safe_name


class WebCacheWriter:
    """Small append-only writer for one Web run directory."""

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.response_dir = self.output_dir / "provider_responses"
        self.pages_dir = self.output_dir / "pages"
        self.response_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, manifest: WebRunManifest) -> None:
        self._write_json(self.output_dir / "manifest.json", manifest)

    def append_query(self, record: WebSearchQueryRecord) -> None:
        self._append_jsonl(self.output_dir / "queries.jsonl", record)

    def append_result(self, record: WebSearchResultRecord) -> None:
        self._append_jsonl(self.output_dir / "search_results.jsonl", record)

    def append_page(self, record: WebPageRecord) -> None:
        self._append_jsonl(self.output_dir / "fetched_pages.jsonl", record)

    def append_evidence(self, record: WebEvidenceRecord) -> None:
        self._append_jsonl(self.output_dir / "web_evidence.jsonl", record)

    def append_extraction_request(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.output_dir / "extraction_requests.jsonl", payload)

    def append_extraction_result(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.output_dir / "extraction_results.jsonl", payload)

    def append_conflict(self, record: WebEvidenceRecord) -> None:
        self._append_jsonl(self.output_dir / "conflicts.jsonl", record)

    def append_skipped_query(self, record: WebSkippedQueryRecord) -> None:
        self._append_jsonl(self.output_dir / "skipped_queries.jsonl", record)

    def write_plan(self, payload: dict[str, Any]) -> None:
        self._write_json(self.output_dir / "plan.json", payload)

    def write_provider_response(self, filename: str, payload: dict[str, Any]) -> str:
        path = self.response_dir / filename
        self._write_json(path, payload)
        return str(path.relative_to(self.output_dir))

    def write_page(self, content_hash: str, text: str, metadata: dict[str, Any]) -> tuple[str, str]:
        md_path = self.pages_dir / f"{content_hash}.md"
        meta_path = self.pages_dir / f"{content_hash}.json"
        md_path.write_text(text, encoding="utf-8")
        self._write_json(meta_path, metadata)
        return str(md_path.relative_to(self.output_dir)), str(meta_path.relative_to(self.output_dir))

    @staticmethod
    def _write_json(path: Path, value: BaseModel | dict[str, Any]) -> None:
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _append_jsonl(path: Path, value: BaseModel | dict[str, Any]) -> None:
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, default=str))
            fh.write("\n")
