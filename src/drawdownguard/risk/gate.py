"""The risk gate: a pure function with no LLM, no network, no broker.

Checks run most-severe first and short-circuit, so the reason returned is
always the most serious violation rather than an arbitrary one.
"""

from drawdownguard.domain import SHARES_PER_CONTRACT, Portfolio, ProposedOrder, Verdict
from drawdownguard.risk.limits import Limits


def veto(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    for check in (
        _permitted_purpose,
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


def _closes_a_short(order: ProposedOrder, portfolio: Portfolio) -> bool:
    """Whether this purchase buys back a short the account already carries."""
    wheel = portfolio.wheels.get(order.symbol)
    if wheel is None:
        return False
    return any(
        leg.is_short
        and leg.right == order.right
        and leg.strike == order.strike
        and leg.expiry == order.expiry
        for leg in wheel.contracts
    )


def _permitted_purpose(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    """What may be sold, and the far narrower question of what may be bought.

    Selling passes to the checks below, which is where it has always been
    governed. Buying is admitted only where it demonstrably takes risk off:
    closing a short the account already carries, or a put standing behind
    shares the client actually owns.

    A put bought against nothing is refused, and the reason is not caution.
    That position is a short bet on the market with extra steps -- it profits
    only if prices fall, and sizing it requires an opinion about whether they
    will. The agent's whole claim is that it holds no such opinion: it answers
    what the book would be worth if the market fell, never whether it will.
    A trade whose payoff depends on that question cannot come from here.

    Calls are not bought at all. Upside purchased with cash is leverage, and no
    shortfall in a downside budget has ever been closed with one.
    """
    if not order.is_purchase:
        return Verdict.approve()

    if _closes_a_short(order, portfolio):
        return Verdict.approve()

    if order.right == "C":
        return Verdict.reject(
            "buying a call is leverage, not protection; the mandate constrains "
            "losses and cannot be repaired by paying for upside"
        )

    wheel = portfolio.wheels.get(order.symbol)
    if wheel is None or wheel.shares <= 0:
        return Verdict.reject(
            f"the portfolio does not hold {order.symbol}, so a put on it is a "
            "directional bet rather than protection"
        )
    return Verdict.approve()


def _must_not_be_naked(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    # A bought option is paid for in full at the moment it is opened, so there
    # is no obligation left to secure. `forbid_naked` governs what the account
    # might be forced to do; a long option can only ever expire.
    if order.is_purchase:
        return Verdict.approve()

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
    if portfolio.drawdown_pct <= limits.max_drawdown_pct:
        return Verdict.approve()

    # Past the limit the account stops taking risk on -- but a purchase that
    # reached this far has already been shown to reduce it, and refusing that
    # is backwards. A deep drawdown is the circumstance the client's promise
    # was written for, and blocking the defence at exactly that moment would
    # make the limit the reason the promise broke.
    if order.is_purchase:
        return Verdict.approve()

    return Verdict.reject(
        f"drawdown {portfolio.drawdown_pct:.1f}% exceeds the limit of "
        f"{limits.max_drawdown_pct:.1f}%; no new risk may be opened"
    )


def _position_concentration(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    if portfolio.equity <= 0:
        return Verdict.reject("equity is zero or negative")
    pct = float(order.capital_at_risk / portfolio.equity * 100)
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
    pct = float(
        (portfolio.deployed + order.capital_at_risk) / portfolio.equity * 100
    )
    if pct > limits.max_deployed_pct:
        return Verdict.reject(
            f"total deployed capital would reach {pct:.1f}%, over the limit of "
            f"{limits.max_deployed_pct:.1f}%"
        )
    return Verdict.approve()


def _net_delta(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    # Measured in dollars of directional exposure against equity, not in share
    # equivalents. A share count is not comparable between a 764 dollar
    # instrument and a 245 dollar one, and means something different at every
    # account size — see `Portfolio.net_delta_value`.
    #
    # Position delta is quantity * per-share delta, and quantity is negative for
    # a short. A short put (delta -0.30, contracts -1) therefore contributes a
    # positive exposure: selling a put is a bullish position.
    if portfolio.equity <= 0:
        return Verdict.reject("equity is zero or negative")
    projected = portfolio.net_delta_value + order.delta_value

    # An order that walks exposure back toward flat is admitted whatever the
    # band says, and this is not a loophole -- it is the only way the band can
    # coexist with a client who holds shares.
    #
    # `net_delta_value` counts the client's own equity, so a mandate that is
    # 80% invested reports 80% before the agent has done anything at all. The
    # band was calibrated for a book that sat in cash and wrote options against
    # it; read literally against a stock portfolio it is breached from the
    # first day, and every protective put -- which is to say every order that
    # would bring it back -- would be refused for making it worse. The client's
    # equity is the premise of the mandate, not risk the agent took on; what
    # the mandate governs about it is the downside budget, which is the entire
    # point of this system.
    #
    # Overshooting is still refused: the projection must land between flat and
    # where the book already is. Buying so much protection that the account
    # ends up net short is a directional bet, not a hedge.
    current = portfolio.net_delta_value
    toward_flat = (
        0.0 <= projected <= current if current >= 0 else current <= projected <= 0.0
    )
    if toward_flat:
        return Verdict.approve()

    # A purchase was admitted upstream on the grounds that it takes risk off.
    # One that carries exposure through flat and out the other side no longer
    # does: past that point every additional contract pays only if the market
    # falls, which is a position on direction rather than a hedge against one.
    # The symmetric band does not catch this on its own -- it is happy to sit
    # 48% short -- so the crossing is refused by name.
    if order.is_purchase and current * projected < 0:
        return Verdict.reject(
            f"exposure would cross from {current:,.0f} through flat to "
            f"{projected:,.0f}; protection may reach neutral, not pass through it"
        )

    pct = abs(projected) / float(portfolio.equity) * 100
    if pct > limits.max_net_delta_pct:
        return Verdict.reject(
            f"directional exposure would reach {pct:.1f}% of equity "
            f"({projected:,.0f}), outside the band of "
            f"+/-{limits.max_net_delta_pct:.1f}%"
        )
    return Verdict.approve()


def _vega(order: ProposedOrder, portfolio: Portfolio, limits: Limits) -> Verdict:
    # No SHARES_PER_CONTRACT here, unlike the delta check above. Delta is quoted
    # per share, vega per contract — see `contract_vega`. The asymmetry is
    # deliberate and load-bearing: adding a factor of 100 to make the two checks
    # look alike would make this limit unreachable.
    #
    # `portfolio.vega` is dollars lost per point of *rising* implied volatility,
    # so it measures being short volatility. Writing adds to it and owning pays
    # it down, which the sign of `contracts` already carries. The magnitude used
    # to be summed instead, and that made a bought hedge look like more of the
    # exact exposure it exists to cancel: an account near its vega ceiling would
    # refuse the protection that would have brought it back under.
    projected = portfolio.vega - order.vega * order.contracts
    if projected > limits.max_vega:
        return Verdict.reject(
            f"vega exposure would reach {projected:.0f}, over the budget of "
            f"{limits.max_vega:.0f}"
        )
    return Verdict.approve()


def _assignment_probability(
    order: ProposedOrder, portfolio: Portfolio, limits: Limits
) -> Verdict:
    # Only the writer can be assigned. Asking this of a purchase would reject
    # the deepest protection for the very property that makes it work, since a
    # put that is likely to finish in the money is a put that is likely to pay.
    if order.is_purchase:
        return Verdict.approve()

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
