"""Compatibility alias for `xft.pipeline.diligence.state`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.diligence.state")
_sys.modules[__name__] = _module
