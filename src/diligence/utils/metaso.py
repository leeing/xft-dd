"""Compatibility alias for `xft.utils.metaso`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.utils.metaso")
_sys.modules[__name__] = _module
