"""Helpers for extracting JSON objects from LLM text responses."""

from __future__ import annotations

import re

THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(raw: str) -> str:
    """Strip <think> blocks and code fences, then extract the outermost JSON object."""
    cleaned = THINK_TAG_RE.sub("", raw).strip()
    fence_match = CODE_FENCE_RE.search(cleaned)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    match = JSON_OBJECT_RE.search(cleaned)
    return match.group(0) if match else cleaned
