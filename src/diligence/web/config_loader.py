"""Compatibility alias for `xft.web.config_loader`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.web.config_loader")
_sys.modules[__name__] = _module
