"""Compatibility alias for `xft.pipeline.recommender.profile_repository`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.pipeline.recommender.profile_repository")
_sys.modules[__name__] = _module
