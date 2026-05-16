"""Shared confidence helpers."""

from __future__ import annotations

from typing import Literal

Confidence = Literal["高", "中", "低", "待补充", "待核实"]

CONFIDENCE_ORDER: dict[str, int] = {"高": 3, "中": 2, "低": 1, "待补充": 0, "待核实": 0}


def normalize_confidence(value: str, *, default: Confidence = "低") -> Confidence:
    """Normalize arbitrary model confidence text to the supported Chinese labels."""
    if value in CONFIDENCE_ORDER:
        return value  # type: ignore[return-value]
    return default
