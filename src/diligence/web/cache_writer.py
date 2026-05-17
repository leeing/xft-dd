"""Compatibility alias for `xft.web.cache_writer`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.web.cache_writer")
_sys.modules[__name__] = _module
