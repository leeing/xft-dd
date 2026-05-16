"""Web enrichment support for the DuckDB-backed recommender."""

from diligence.recommender.web.runner import run_web_enrichment
from diligence.recommender.web.web_loader import load_web_cache_to_duckdb

__all__ = ["load_web_cache_to_duckdb", "run_web_enrichment"]

