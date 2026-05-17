"""Load scoring policy from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from xft.core.scenario import maybe_scenario_path
from xft.scoring.models import ScoringPolicy


def load_scoring_policy(path: str | Path | None = None) -> ScoringPolicy:
    """Load scoring policy from YAML.

    Defaults to ``config/scoring_policy.yaml`` relative to the project root.
    """
    if path is None:
        path = Path("config/scoring_policy.yaml")
    else:
        scenario = maybe_scenario_path(path)
        if scenario is not None:
            return load_scoring_policy(scenario.scoring_policy_path)
        path = Path(path)

    if not path.exists():
        return ScoringPolicy()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ScoringPolicy()

    return ScoringPolicy.model_validate(data)
