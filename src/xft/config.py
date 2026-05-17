"""Compatibility exports for the diligence pipeline config."""

from xft.pipeline.diligence.config import (
    AppConfig,
    BatchConfig,
    Dimension,
    ExtractField,
    ReportOptions,
    load_config,
    validate_dimension_ids,
)

__all__ = [
    "AppConfig",
    "BatchConfig",
    "Dimension",
    "ExtractField",
    "ReportOptions",
    "load_config",
    "validate_dimension_ids",
]
