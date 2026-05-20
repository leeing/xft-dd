"""Pydantic models for the business-first recommender."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RecommendationRunResult(BaseModel):
    """Public result returned by run_recommendation."""

    company_name: str
    status: Literal["success", "partial", "failed"]
    run_id: str
    output_dir: str
    report_path: str | None = None
    result_path: str | None = None
    error: str | None = None
