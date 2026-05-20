"""Architectural boundary checks for the xft package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_files(path: str) -> list[Path]:
    return sorted((ROOT / path).rglob("*.py"))


def test_web_does_not_import_recommender() -> None:
    forbidden = "xft.pipeline.recommender"
    offenders: list[str] = []
    for path in _python_files("src/xft/web"):
        text = path.read_text(encoding="utf-8")
        if forbidden in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_web_cache_utils_do_not_import_recommender() -> None:
    forbidden = "xft.pipeline.recommender"
    offenders: list[str] = []
    for path in [*_python_files("src/xft/web"), *_python_files("src/xft/cache"), *_python_files("src/xft/utils")]:
        text = path.read_text(encoding="utf-8")
        if forbidden in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_core_imports_without_recommender_side_effects() -> None:
    from xft.core.search_models import SearchItem, make_item_id
    from xft.core.scenario import load_scenario
    from xft.warehouse.profile_repository import CompanyProfileRepository

    assert SearchItem.__name__ == "SearchItem"
    assert make_item_id.__name__ == "make_item_id"
    assert load_scenario.__name__ == "load_scenario"
    assert CompanyProfileRepository.__name__ == "CompanyProfileRepository"
