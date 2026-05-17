"""Compatibility alias for `xft.pipeline.diligence.crawler_mode`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.diligence.crawler_mode")
_sys.modules[__name__] = _module
