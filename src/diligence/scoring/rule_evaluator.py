"""Compatibility alias for `xft.scoring.rule_evaluator`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.scoring.rule_evaluator")
_sys.modules[__name__] = _module
