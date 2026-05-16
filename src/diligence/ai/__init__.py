"""Shared AI utilities used by legacy report and recommender flows."""

from diligence.ai.client import get_ai_client
from diligence.ai.json_extractor import THINK_TAG_RE, extract_json

__all__ = ["THINK_TAG_RE", "extract_json", "get_ai_client"]
