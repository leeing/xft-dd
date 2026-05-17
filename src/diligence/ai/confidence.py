"""Compatibility alias for `xft.ai.confidence`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.ai.confidence")
_sys.modules[__name__] = _module
