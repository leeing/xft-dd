"""Shared helpers for recommender evidence maps."""

from __future__ import annotations

from typing import Any


def merge_indicator_evidence(
    local: dict[str, list[dict[str, Any]]],
    web: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge local and Web evidence keyed by business indicator."""
    merged = {key: list(value) for key, value in local.items()}
    for key, items in web.items():
        merged.setdefault(key, []).extend(items)
    return merged
