"""Compatibility alias for `xft.pipeline.recommender.dimension_analyzer`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.recommender.dimension_analyzer")
_sys.modules[__name__] = _module
