"""Limit definitions, loaded from config/risk.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class Limits(BaseModel):
    max_position_pct: float
    max_deployed_pct: float
    max_drawdown_pct: float
    max_net_delta: float
    max_vega: float
    max_assignment_prob: float
    min_open_interest: int
    max_spread_pct: float
    forbid_naked: bool = True


def load_limits(path: Path | str = "config/risk.yaml") -> Limits:
    return Limits(**yaml.safe_load(Path(path).read_text()))
