from datetime import date
from decimal import Decimal

import numpy as np

from flywheel.domain import Portfolio
from flywheel.optimizer.candidates import Candidate
from flywheel.optimizer.model import optimize
from flywheel.risk.limits import Limits

LIMITS = Limits(
    max_position_pct=25.0,
    max_deployed_pct=60.0,
    max_drawdown_pct=15.0,
    max_net_delta_pct=50.0,
    max_vega=500.0,
    max_assignment_prob=0.35,
    min_open_interest=500,
    max_spread_pct=5.0,
    forbid_naked=True,
)
RNG = np.random.default_rng(7)


def candidate(strike=100.0, mid="1.00", delta=-0.30, vega=10.0, tail=-0.02):
    """One tradable short put, priced and with a loss distribution attached.

    `tail` shifts the terminal price distribution: at -0.02 the underlying lands
    near 98, so a 100 strike finishes about 2 in the money and every scenario is
    a small loss. That is deliberate — a candidate that never loses would make
    the CVaR constraint unreachable and the tail-risk test vacuous.
    """
    losses = (
        -float(mid) * 100
        + np.maximum(strike - 100.0 * (1 + RNG.normal(tail, 0.01, 400)), 0.0) * 100
    )
    return Candidate(
        symbol="SPY",
        right="P",
        spot=100.0,
        occ_symbol=f"SPY260904P{int(strike)}",
        strike=Decimal(str(strike)),
        expiry=date(2026, 9, 4),
        bid=Decimal(mid),
        ask=Decimal(mid),
        mid=Decimal(mid),
        spread_pct=0.5,
        open_interest=5000,
        implied_vol=0.18,
        tau=0.03,
        delta=delta,
        vega=vega,
        assignment_prob=0.25,
        collateral=Decimal(str(strike)) * 100,
        losses=losses,
    )


def portfolio(equity="1000000"):
    return Portfolio(
        equity=Decimal(equity), cash=Decimal(equity), peak_equity=Decimal(equity)
    )


def test_an_empty_candidate_set_yields_an_empty_allocation():
    assert optimize([], portfolio(), LIMITS, Decimal("100000"), 5000.0) == []


def test_the_capital_budget_is_respected():
    allocations = optimize([candidate()], portfolio(), LIMITS, Decimal("25000"), 1e9)
    spent = sum(a.contracts * a.candidate.collateral for a in allocations)
    assert spent <= Decimal("25000")


def test_the_richer_premium_is_preferred_at_equal_risk():
    cheap = candidate(strike=100.0, mid="0.50")
    rich = candidate(strike=100.0, mid="2.00")
    allocations = optimize([cheap, rich], portfolio(), LIMITS, Decimal("10000"), 1e9)
    chosen = {a.candidate.mid: a.contracts for a in allocations}
    assert chosen.get(Decimal("2.00"), 0) >= chosen.get(Decimal("0.50"), 0)


def test_the_delta_band_is_respected():
    allocations = optimize(
        [candidate(delta=-0.40)], portfolio(), LIMITS, Decimal("10000000"), 1e9
    )
    # In dollars now, not share equivalents: the constraint the optimizer
    # solves and the one the gate enforces must be the same quantity, or
    # the optimizer proposes what the gate then refuses.
    book = portfolio()
    net = sum(
        -a.candidate.delta * a.contracts * 100 * a.candidate.spot for a in allocations
    )
    budget = float(book.equity) * LIMITS.max_net_delta_pct / 100
    assert abs(net) <= budget + 1e-6


def test_the_vega_budget_is_respected():
    allocations = optimize(
        [candidate(vega=50.0)], portfolio(), LIMITS, Decimal("10000000"), 1e9
    )
    total = sum(abs(a.candidate.vega) * a.contracts for a in allocations)
    assert total <= LIMITS.max_vega + 1e-6


def test_a_tight_cvar_limit_forces_a_smaller_book():
    loose = optimize([candidate()], portfolio(), LIMITS, Decimal("1000000"), 1e9)
    tight = optimize([candidate()], portfolio(), LIMITS, Decimal("1000000"), 500.0)
    assert sum(a.contracts for a in tight) < sum(a.contracts for a in loose)


def test_every_allocation_has_a_positive_contract_count():
    allocations = optimize(
        [candidate(), candidate(strike=95.0)],
        portfolio(),
        LIMITS,
        Decimal("100000"),
        1e9,
    )
    assert all(a.contracts > 0 for a in allocations)


def test_a_zero_budget_returns_empty_rather_than_raising():
    allocations = optimize([candidate()], portfolio(), LIMITS, Decimal("0"), 1e9)
    assert allocations == []
