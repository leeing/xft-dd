"""Compatibility alias for `xft.web.providers`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.web.providers")
_sys.modules[__name__] = _module
