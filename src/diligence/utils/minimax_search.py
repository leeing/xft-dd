"""Compatibility alias for `xft.utils.minimax_search`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.utils.minimax_search")
_sys.modules[__name__] = _module
