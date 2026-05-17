"""Compatibility alias for `xft.cache.hashing`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.cache.hashing")
_sys.modules[__name__] = _module
