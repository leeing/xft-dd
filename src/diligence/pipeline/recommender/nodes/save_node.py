"""Compatibility alias for `xft.pipeline.recommender.nodes.save_node`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.recommender.nodes.save_node")
_sys.modules[__name__] = _module
