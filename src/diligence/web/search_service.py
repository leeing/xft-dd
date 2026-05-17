"""Compatibility alias for `xft.web.search_service`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.web.search_service")
_sys.modules[__name__] = _module
