"""Architectural boundary checks for the xft package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_files(path: str) -> list[Path]:
    return sorted((ROOT / path).rglob("*.py"))


def test_web_and_scoring_do_not_import_recommender() -> None:
    forbidden = "xft.pipeline.recommender"
    offenders: list[str] = []
    for path in [*_python_files("src/xft/web"), *_python_files("src/xft/scoring")]:
        text = path.read_text(encoding="utf-8")
        if forbidden in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_core_imports_without_recommender_side_effects() -> None:
    from xft.core.config_loader import load_dimensions_config
    from xft.core.dimension_analyzer import analyze_dimensions
    from xft.core.models import DimensionAnalysis, ScoringSubject
    from xft.core.scenario import load_scenario
    from xft.warehouse.profile_repository import CompanyProfileRepository

    assert load_dimensions_config
    assert analyze_dimensions
    assert DimensionAnalysis
    assert ScoringSubject
    assert load_scenario
    assert CompanyProfileRepository
