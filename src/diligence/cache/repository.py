"""Compatibility alias for `xft.cache.repository`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.cache.repository")
_sys.modules[__name__] = _module
