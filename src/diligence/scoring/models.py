"""Compatibility alias for `xft.scoring.models`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.scoring.models")
_sys.modules[__name__] = _module
