"""The allocation problem: which contracts to sell, and how many.

A MILP over integer contract counts. Tail risk enters through the
Rockafellar-Uryasev CVaR formulation, which stays linear in the decision
variables.
"""

from decimal import Decimal

import cvxpy as cp
import numpy as np
from pydantic import BaseModel, ConfigDict

from flywheel.domain import SHARES_PER_CONTRACT, Portfolio
from flywheel.optimizer.candidates import Candidate
from flywheel.risk.limits import Limits

CVAR_ALPHA = 0.95
MAX_CONTRACTS_PER_LEG = 20


class Allocation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: Candidate
    contracts: int


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
    losses = np.vstack([c.losses for c in candidates]).T  # scenarios x candidates
    scenarios = losses.shape[0]

    premium = np.array([float(c.mid) * SHARES_PER_CONTRACT for c in candidates])
    collateral = np.array([float(c.collateral) for c in candidates])
    # x counts contracts sold, so it is positive where the position quantity is
    # negative. Position delta is quantity * per-share delta, hence the sign flip:
    # selling a -0.30 delta put contributes +30.
    delta_contribution = np.array([-c.delta * SHARES_PER_CONTRACT for c in candidates])
    vega = np.array([abs(c.vega) for c in candidates])

    x = cp.Variable(n, integer=True)
    zeta = cp.Variable()
    slack = cp.Variable(scenarios, nonneg=True)

    portfolio_loss = losses @ x
    cvar = zeta + cp.sum(slack) / ((1 - CVAR_ALPHA) * scenarios)

    constraints = [
        x >= 0,
        x <= MAX_CONTRACTS_PER_LEG,
        slack >= portfolio_loss - zeta,
        collateral @ x <= float(capital_budget),
        # Every candidate in one call belongs to one symbol, so a single
        # constraint on total collateral is the per-symbol concentration cap.
        collateral @ x <= float(portfolio.equity) * limits.max_position_pct / 100,
        delta_contribution @ x <= limits.max_net_delta - portfolio.net_delta,
        delta_contribution @ x >= -limits.max_net_delta - portfolio.net_delta,
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
