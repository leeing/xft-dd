"""Small helpers for recording LLM calls during recommendation runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

RAW_PREVIEW_CHARS = 500


def preview_text(text: str, limit: int = RAW_PREVIEW_CHARS) -> str:
    """Return a compact one-line preview suitable for terminal and JSONL logs."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def exception_summary(exc: BaseException) -> str:
    """Return a compact exception label without losing the error type."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text[:180]}" if text else type(exc).__name__


def llm_event(  # noqa: PLR0913
    *,
    stage: str,
    name: str,
    model: str,
    status: str,
    elapsed_seconds: float,
    request: dict[str, Any] | None = None,
    response_preview: str = "",
    result: str = "",
    confidence: str = "",
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Build a stable serializable event for llm_calls.jsonl."""
    payload: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "stage": stage,
        "name": name,
        "model": model,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "request": request or {},
        "response_preview": response_preview,
        "result": result,
        "confidence": confidence,
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)[:500]
    return payload
