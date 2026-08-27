"""The allocation problem: which contracts to sell, and how many.

A MILP over integer contract counts. Tail risk enters through the
Rockafellar-Uryasev CVaR formulation, which stays linear in the decision
variables.
"""

from decimal import Decimal

import cvxpy as cp
import numpy as np
from pydantic import BaseModel, ConfigDict

from drawdownguard.domain import SHARES_PER_CONTRACT, Portfolio
from drawdownguard.optimizer.candidates import Candidate
from drawdownguard.risk.limits import Limits

CVAR_ALPHA = 0.95
MAX_CONTRACTS_PER_LEG = 20


class Allocation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: Candidate
    contracts: int


def _covered_call_cap(candidates: list[Candidate], portfolio: Portfolio) -> int:
    """How many call contracts the shares on hand can cover.

    Returns a large number for puts, which are secured by cash and constrained
    by the capital budget instead. Zero means the position cannot write a
    covered call at all, and the caller should not trade rather than propose
    something the gate is obliged to refuse.
    """
    if not candidates or candidates[0].right != "C":
        return MAX_CONTRACTS_PER_LEG * max(len(candidates), 1)
    wheel = portfolio.wheels.get(candidates[0].symbol)
    shares = getattr(wheel, "shares", 0) if wheel else 0
    return int(shares) // SHARES_PER_CONTRACT


def optimize(
    candidates: list[Candidate],
    portfolio: Portfolio,
    limits: Limits,
    capital_budget: Decimal,
    cvar_limit: float,
) -> list[Allocation]:
    """Choose contracts maximising premium subject to the risk constraints.

    Returns an empty list when the problem is infeasible or the solver fails.
    Never raises: an unsolved cycle must skip trading, not crash the agent.
    """
    if not candidates:
        return []

    n = len(candidates)

    # A covered call cannot exceed the shares that cover it. Without this the
    # optimizer sizes calls exactly as it sizes puts -- from the capital budget
    # and the greek limits -- and proposes more contracts than the position can
    # cover. The gate then refuses every one of them as naked, correctly, and
    # nothing is ever tried at a size that would have worked.
    #
    # That is not hypothetical. It cost this project 819 days: an assignment of
    # one contract left 100 shares, the optimizer kept proposing four, the gate
    # kept refusing "naked call: 100 shares held, 400 required", and the wheel
    # stood still for two years while the backtest reported a cautious strategy
    # that chose not to trade.
    covered_cap = _covered_call_cap(candidates, portfolio)
    if covered_cap == 0:
        return []

    losses = np.vstack([c.losses for c in candidates]).T  # scenarios x candidates
    scenarios = losses.shape[0]

    premium = np.array([float(c.mid) * SHARES_PER_CONTRACT for c in candidates])
    collateral = np.array([float(c.collateral) for c in candidates])
    # x counts contracts sold, so it is positive where the position quantity is
    # negative. Position delta is quantity * per-share delta, hence the sign flip:
    # selling a -0.30 delta put contributes +30.
    # Dollars of directional exposure per contract sold, not share
    # equivalents. The gate measures the same quantity the same way; if the
    # two units diverged the optimizer would keep proposing allocations the
    # gate then refused, and the refusals would look like bad luck.
    delta_contribution = np.array(
        [-c.delta * SHARES_PER_CONTRACT * c.spot for c in candidates]
    )
    # Vega is already per contract, so unlike delta it takes no share factor.
    # See `contract_vega` for why the two greeks are quoted differently.
    vega = np.array([abs(c.vega) for c in candidates])

    delta_budget = float(portfolio.equity) * limits.max_net_delta_pct / 100

    x = cp.Variable(n, integer=True)
    zeta = cp.Variable()
    slack = cp.Variable(scenarios, nonneg=True)

    portfolio_loss = losses @ x
    cvar = zeta + cp.sum(slack) / ((1 - CVAR_ALPHA) * scenarios)

    constraints = [
        x >= 0,
        x <= MAX_CONTRACTS_PER_LEG,
        cp.sum(x) <= covered_cap,
        slack >= portfolio_loss - zeta,
        collateral @ x <= float(capital_budget),
        # Every candidate in one call belongs to one symbol, so a single
        # constraint on total collateral is the per-symbol concentration cap.
        collateral @ x <= float(portfolio.equity) * limits.max_position_pct / 100,
        delta_contribution @ x <= delta_budget - portfolio.net_delta_value,
        delta_contribution @ x >= -delta_budget - portfolio.net_delta_value,
        vega @ x <= limits.max_vega - portfolio.vega,
        cvar <= cvar_limit,
    ]

    problem = cp.Problem(cp.Maximize(premium @ x), constraints)
    try:
        problem.solve(solver=cp.HIGHS)
    except cp.error.SolverError:
        return []

    if problem.status not in ("optimal", "optimal_inaccurate") or x.value is None:
        return []

    return [
        Allocation(candidate=candidate, contracts=int(round(count)))
        for candidate, count in zip(candidates, x.value, strict=True)
        if round(count) >= 1
    ]
