"""Load business-facing recommendation configuration."""

from __future__ import annotations

from pathlib import Path

from xft.core.config_loader import read_yaml
from xft.core.scenario import maybe_scenario_path
from xft.pipeline.recommender.business_models import BusinessRecommendationConfig


def load_business_recommendation_config(path: str | Path | None) -> BusinessRecommendationConfig | None:
    """Load optional business recommendation config."""
    if path is None:
        return None
    scenario = maybe_scenario_path(path)
    if scenario is not None:
        return load_business_recommendation_config(scenario.business_modules_path)
    config_path = Path(path)
    if config_path.is_dir():
        config_path = config_path / "business_modules.yaml"
    if not config_path.exists():
        return None
    return BusinessRecommendationConfig.model_validate(read_yaml(config_path))
