"""Compatibility alias for `xft.pipeline.recommender.config_loader`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.recommender.config_loader")
_sys.modules[__name__] = _module
