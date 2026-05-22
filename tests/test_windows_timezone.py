from __future__ import annotations

import importlib
import sys
from types import ModuleType
from zoneinfo import ZoneInfoNotFoundError

import pytest


def test_top_level_cli_import_does_not_require_system_tzdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows Python may not have IANA tzdata installed."""

    import zoneinfo

    def missing_zoneinfo(key: str) -> object:
        msg = f"No time zone found with key {key}"
        raise ZoneInfoNotFoundError(msg)

    modules = [
        "xft.cli.main",
        "xft.cli.recommend",
        "xft.pipeline.recommender.batch",
        "xft.pipeline.recommender.graph",
    ]
    removed: dict[str, ModuleType] = {}
    for name in modules:
        if name in sys.modules:
            removed[name] = sys.modules.pop(name)

    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing_zoneinfo)
    try:
        module = importlib.import_module("xft.cli.main")
    finally:
        for name in modules:
            sys.modules.pop(name, None)
        sys.modules.update(removed)

    assert hasattr(module, "main")
