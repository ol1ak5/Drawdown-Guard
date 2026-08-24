"""Limit definitions, loaded from config/risk.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel


class Limits(BaseModel):
    max_position_pct: float
    max_deployed_pct: float
    max_drawdown_pct: float
    # Directional exposure band, as a percentage of equity, plus or minus.
    # Denominated in equity rather than share equivalents: see the note on
    # `Portfolio.net_delta_value` for why a share count is not comparable
    # across instruments or across account sizes.
    max_net_delta_pct: float
    max_vega: float
    max_assignment_prob: float
    min_open_interest: int
    max_spread_pct: float
    forbid_naked: bool = True


def load_limits(path: Path | str = "config/risk.yaml") -> Limits:
    return Limits(**yaml.safe_load(Path(path).read_text()))
