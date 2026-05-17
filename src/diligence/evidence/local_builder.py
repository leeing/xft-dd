"""Compatibility alias for `xft.evidence.local_builder`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.evidence.local_builder")
_sys.modules[__name__] = _module
