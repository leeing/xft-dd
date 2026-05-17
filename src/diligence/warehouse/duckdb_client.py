"""Compatibility alias for `xft.warehouse.duckdb_client`."""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("xft.warehouse.duckdb_client")
_sys.modules[__name__] = _module
