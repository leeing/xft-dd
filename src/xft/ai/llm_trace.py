"""Small helpers for recording LLM calls during recommendation runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from xft.progress import display

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
    response_text: str = "",
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
        "response_text": response_text,
        "result": result,
        "confidence": confidence,
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)
    return payload


def print_llm_start(*, title: str, model: str, request: dict[str, Any]) -> None:
    display.raw(
        "\n".join(
            [
                f"  ┌─ LLM 开始：{title}",
                f"  │ model: {model}",
                "  │ request:",
                _indent(_json(request), "  │   "),
                "  └─ 等待模型返回...",
            ]
        )
        + "\n"
    )


def print_llm_success(
    *,
    title: str,
    elapsed_seconds: float,
    result: str = "",
    confidence: str = "",
    raw: str,
) -> None:
    rows = [
        f"  ┌─ LLM 完成：{title}",
        f"  │ elapsed: {elapsed_seconds:.2f}s",
    ]
    if result:
        rows.append(f"  │ result: {result}")
    if confidence:
        rows.append(f"  │ confidence: {confidence}")
    rows.extend(
        [
            "  │ raw_response:",
            _indent(raw or "<empty>", "  │   "),
            "  └─ end",
        ]
    )
    display.raw("\n".join(rows) + "\n")


def print_llm_failure(
    *,
    title: str,
    elapsed_seconds: float,
    error: BaseException,
    fallback: str,
) -> None:
    display.raw(
        "\n".join(
            [
                f"  ┌─ LLM 失败：{title}",
                f"  │ elapsed: {elapsed_seconds:.2f}s",
                f"  │ error_type: {type(error).__name__}",
                "  │ error:",
                _indent(str(error) or type(error).__name__, "  │   "),
                f"  │ fallback: {fallback}",
                "  └─ end",
            ]
        )
        + "\n"
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
