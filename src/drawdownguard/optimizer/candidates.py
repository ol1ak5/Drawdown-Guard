"""Chain rows in, scored candidates out.

Liquidity filters live here rather than in the optimizer: a contract that
fails them is not a worse choice, it is not a choice at all.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from drawdownguard.domain import SHARES_PER_CONTRACT, Right
from drawdownguard.optimizer.payoff import (
    assignment_prob,
    bs_delta,
    contract_vega,
    loss_scenarios,
)
from drawdownguard.risk.limits import Limits

TRADING_DAYS = 252.0


class Candidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    right: Right
    occ_symbol: str
    # Underlying price when the chain was read. Carried so directional
    # exposure can be priced in dollars; share equivalents cannot be
    # converted afterwards without knowing what a share cost.
    spot: float
    strike: Decimal
    expiry: date
    bid: Decimal
    ask: Decimal
    mid: Decimal
    spread_pct: float
    open_interest: int
    implied_vol: float
    tau: float
    delta: float  # per share, signed
    vega: float  # per contract, per one point of implied volatility
    assignment_prob: float
    collateral: Decimal
    losses: np.ndarray = Field(exclude=True)

    @property
    def premium_per_contract(self) -> Decimal:
        return self.mid * SHARES_PER_CONTRACT


def build_candidates(
    chain_rows: list[dict[str, Any]],
    spot: float,
    symbol: str,
    right: Right,
    as_of: date,
    limits: Limits,
    returns: np.ndarray,
    target_delta: tuple[float, float],
) -> list[Candidate]:
    """Filter and price a chain. Returns only tradable candidates."""
    low, high = target_delta
    candidates: list[Candidate] = []

    for row in chain_rows:
        bid, ask = Decimal(row["bid"]), Decimal(row["ask"])
        if bid <= 0 or ask <= 0:
            continue

        days = (row["expiry"] - as_of).days
        if days <= 0:
            continue

        if row["open_interest"] < limits.min_open_interest:
            continue

        mid = (bid + ask) / 2
        spread_pct = float((ask - bid) / mid * 100)
        if spread_pct > limits.max_spread_pct:
            continue

        strike = float(row["strike"])
        tau = days / 365.0
        vol = float(row["implied_vol"])

        delta = bs_delta(spot, strike, tau, vol, right)
        if not low <= abs(delta) <= high:
            continue

        scaled_returns = np.asarray(returns) * np.sqrt(days)
        candidates.append(
            Candidate(
                symbol=symbol,
                right=right,
                occ_symbol=row["occ_symbol"],
                spot=spot,
                strike=row["strike"],
                expiry=row["expiry"],
                bid=bid,
                ask=ask,
                mid=mid,
                spread_pct=spread_pct,
                open_interest=row["open_interest"],
                implied_vol=vol,
                tau=tau,
                delta=delta,
                vega=contract_vega(spot, strike, tau, vol),
                assignment_prob=assignment_prob(spot, strike, tau, vol, right),
                collateral=row["strike"] * SHARES_PER_CONTRACT,
                losses=loss_scenarios(
                    spot, strike, tau, float(mid), scaled_returns, right
                ),
            )
        )

    return candidates
