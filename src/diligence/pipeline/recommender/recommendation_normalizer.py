"""Compatibility alias for `xft.pipeline.recommender.recommendation_normalizer`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.recommender.recommendation_normalizer")
_sys.modules[__name__] = _module
