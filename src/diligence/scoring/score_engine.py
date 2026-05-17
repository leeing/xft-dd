"""Compatibility alias for `xft.scoring.score_engine`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.scoring.score_engine")
_sys.modules[__name__] = _module
