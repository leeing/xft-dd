"""Shared CLI helpers."""

from __future__ import annotations


def csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated CLI value."""
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]
