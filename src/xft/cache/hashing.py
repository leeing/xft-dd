"""Stable hashing helpers for the SQL cache."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_BLANK_LINES_RE = re.compile(r"\n{3,}")


def stable_hash(value: str) -> str:
    """Return a sha256 hex digest for a UTF-8 string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_json_hash(value: dict[str, Any]) -> str:
    """Hash a JSON-compatible dict with stable key ordering."""
    dumped = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return stable_hash(dumped)


def normalize_markdown(markdown: str) -> str:
    """Lightly normalise markdown before content hashing."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    return _BLANK_LINES_RE.sub("\n\n", text)


def content_hash(markdown: str) -> str:
    """Hash the markdown content actually consumed by downstream extraction."""
    return stable_hash(normalize_markdown(markdown))
