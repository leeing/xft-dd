"""Shared utility functions used across the xft package."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def get_nested(value: Any, path: str) -> Any:
    """Traverse a dotted path into nested dicts. Returns None on missing keys."""
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def contains(haystack: Any, needle: Any) -> bool:
    """Recursively check whether needle is present in haystack.

    For strings, does substring matching. For dicts, checks values. For
    lists/iterables, checks each element.
    """
    if haystack is None:
        return False
    if isinstance(haystack, str):
        return str(needle) in haystack
    if isinstance(haystack, dict):
        return contains(list(haystack.values()), needle)
    if isinstance(haystack, Iterable):
        return any(str(needle) in str(item) for item in haystack)
    return str(needle) in str(haystack)


def result_text(result: str) -> str:
    """Map indicator result enum values to Chinese display text."""
    return {
        "matched": "满足",
        "possible": "可能满足",
        "not_matched": "不满足",
        "unknown": "证据不足",
    }.get(result, result)
