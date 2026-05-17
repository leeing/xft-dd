"""Compatibility alias for `xft.warehouse.schema`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.warehouse.schema")
_sys.modules[__name__] = _module
