"""Timezone helpers that work on platforms without system IANA tzdata."""

from __future__ import annotations

from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def shanghai_tz() -> tzinfo:
    """Return Asia/Shanghai, falling back to fixed UTC+8 on Windows without tzdata."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")
