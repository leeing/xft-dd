"""Cache index helpers for data/web run directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExistingWebRun:
    """A reusable Web enrichment run discovered on disk."""

    run_dir: Path
    queries: int
    results: int
    evidence: int


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


def count_jsonl_rows(path: Path) -> int:
    """Count non-empty JSONL rows without parsing the file."""
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
