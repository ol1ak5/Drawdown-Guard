"""The risk gate: a pure function with no LLM, no network, no broker.

Checks run most-severe first and short-circuit, so the reason returned is
always the most serious violation rather than an arbitrary one.
"""

from flywheel.domain import SHARES_PER_CONTRACT, Portfolio, ProposedOrder, Verdict
from flywheel.risk.limits import Limits


def veto(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    for check in (
        _must_be_short,
        _must_not_be_naked,
        _drawdown,
        _position_concentration,
        _total_deployed,
        _net_delta,
        _vega,
        _assignment_probability,
        _open_interest,
        _spread,
    ):
        verdict = check(order, portfolio, limits)
        if not verdict.approved:
            return verdict
    return Verdict.approve()


def _must_be_short(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if order.contracts >= 0:
        return Verdict.reject(
            "this strategy only sells to open; a short position is required"
        )
    return Verdict.approve()


def _must_not_be_naked(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if not limits.forbid_naked:
        return Verdict.approve()

    wheel = portfolio.wheels.get(order.symbol)
    quantity = abs(order.contracts)

    if order.right == "C":
        held = wheel.shares if wheel else 0
        required = quantity * SHARES_PER_CONTRACT
        if held < required:
            return Verdict.reject(
                f"naked call: {held} shares of {order.symbol} held, "
                f"{required} required to cover"
            )
        return Verdict.approve()

    required_cash = order.collateral
    if portfolio.cash < required_cash:
        return Verdict.reject(
            f"put is not cash-secured: {required_cash} of cash required, "
            f"{portfolio.cash} available"
        )
    return Verdict.approve()


def _drawdown(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    if portfolio.drawdown_pct > limits.max_drawdown_pct:
        return Verdict.reject(
            f"drawdown {portfolio.drawdown_pct:.1f}% exceeds the limit of "
            f"{limits.max_drawdown_pct:.1f}%; no new positions"
        )
    return Verdict.approve()


def _position_concentration(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if portfolio.equity <= 0:
        return Verdict.reject("equity is zero or negative")
    pct = float(order.collateral / portfolio.equity * 100)
    if pct > limits.max_position_pct:
        return Verdict.reject(
            f"position size {pct:.1f}% of equity exceeds the per-instrument "
            f"limit of {limits.max_position_pct:.1f}%"
        )
    return Verdict.approve()


def _total_deployed(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if portfolio.equity <= 0:
        return Verdict.reject("equity is zero or negative")
    pct = float((portfolio.deployed + order.collateral) / portfolio.equity * 100)
    if pct > limits.max_deployed_pct:
        return Verdict.reject(
            f"total deployed capital would reach {pct:.1f}%, over the limit of "
            f"{limits.max_deployed_pct:.1f}%"
        )
    return Verdict.approve()


def _net_delta(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    # Position delta is quantity * per-share delta, and quantity is negative for
    # a short. A short put (delta -0.30, contracts -1) therefore contributes
    # +30: selling a put is a bullish position.
    contributed = order.delta * order.contracts * SHARES_PER_CONTRACT
    projected = portfolio.net_delta + contributed
    if abs(projected) > limits.max_net_delta:
        return Verdict.reject(
            f"net delta would reach {projected:.0f}, outside the band of "
            f"+/-{limits.max_net_delta:.0f}"
        )
    return Verdict.approve()


def _vega(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    # No SHARES_PER_CONTRACT here, unlike the delta check above. Delta is quoted
    # per share, vega per contract — see `contract_vega`. The asymmetry is
    # deliberate and load-bearing: adding a factor of 100 to make the two checks
    # look alike would make this limit unreachable.
    projected = portfolio.vega + abs(order.vega * order.contracts)
    if projected > limits.max_vega:
        return Verdict.reject(
            f"vega exposure would reach {projected:.0f}, over the budget of "
            f"{limits.max_vega:.0f}"
        )
    return Verdict.approve()


def _assignment_probability(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if order.assignment_prob > limits.max_assignment_prob:
        return Verdict.reject(
            f"assignment probability {order.assignment_prob:.2f} exceeds the "
            f"budget of {limits.max_assignment_prob:.2f}"
        )
    return Verdict.approve()


def _open_interest(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if order.open_interest < limits.min_open_interest:
        return Verdict.reject(
            f"open interest {order.open_interest} is below the floor of "
            f"{limits.min_open_interest}"
        )
    return Verdict.approve()


def _spread(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    if order.spread_pct > limits.max_spread_pct:
        return Verdict.reject(
            f"spread {order.spread_pct:.1f}% is wider than the ceiling of "
            f"{limits.max_spread_pct:.1f}%"
        )
    return Verdict.approve()
