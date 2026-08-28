"""The risk gate: a pure function with no LLM, no network, no broker.

Checks run most-severe first and short-circuit, so the reason returned is
always the most serious violation rather than an arbitrary one.
"""

from decimal import Decimal

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


def short_quantity(order: ProposedOrder, portfolio: Portfolio) -> int:
    """How many of this exact contract the account is short. Zero if none."""
    position = portfolio.positions.get(order.symbol)
    if position is None:
        return 0
    return sum(
        abs(leg.contracts)
        for leg in position.contracts
        if leg.is_short
        and leg.right == order.right
        and leg.strike == order.strike
        and leg.expiry == order.expiry
    )


def closes_a_short(order: ProposedOrder, portfolio: Portfolio) -> bool:
    """Whether this purchase buys back a short the account already carries."""
    return short_quantity(order, portfolio) > 0


def long_quantity(order: ProposedOrder, portfolio: Portfolio) -> int:
    """How many of this exact contract the account holds long. Zero if none."""
    position = portfolio.positions.get(order.symbol)
    if position is None:
        return 0
    return sum(
        leg.contracts
        for leg in position.contracts
        if not leg.is_short
        and leg.right == order.right
        and leg.strike == order.strike
        and leg.expiry == order.expiry
    )


def closes_a_long(order: ProposedOrder, portfolio: Portfolio) -> bool:
    """Whether this sale hands back a long the account already carries.

    The whole sale, not part of it. A sale larger than the long opens a short
    for the surplus, and calling that "closing" is how a naked position gets
    through a check written to allow a handback.
    """
    if order.is_purchase:
        return False
    return long_quantity(order, portfolio) >= abs(order.contracts)


def opening_contracts(order: ProposedOrder, portfolio: Portfolio) -> int:
    """How much of a sale actually opens a short. Zero when it only closes.

    `ProposedOrder` cannot answer this alone -- a sale of a put is a written
    put or a handback of one already owned, and the two are the same symbol,
    side and size. Only the book tells them apart, so the book is asked here
    and the answer is used by every check that prices an obligation.
    """
    if order.is_purchase:
        return 0
    return max(abs(order.contracts) - long_quantity(order, portfolio), 0)


def capital_at_risk(order: ProposedOrder, portfolio: Portfolio) -> Decimal:
    """What this order commits, counting only the part that opens something.

    `ProposedOrder.capital_at_risk` charges every sale the whole strike,
    because a written put must be able to buy the shares. A handback buys
    nothing: the contracts leave the account and the capital they tied up comes
    back. Charging it anyway made the release of three 560 puts read as a
    168,000 position -- 56% of a 300,000 account against a 25% cap -- so the
    concentration limit refused the order that was giving capital back.

    A written *call* commits no cash either, and for a different reason:
    `ProposedOrder.collateral` says so in its own docstring -- "calls are
    collateralised by shares" -- but `capital_at_risk` reached for that number
    anyway. `_must_not_be_naked` has already refused any call the shares do not
    cover, so a call arriving here is covered by stock the book is holding and
    the ladder is already counting. Charging it the strike as well counts the
    same position twice and calls the second copy new risk.

    It is not theoretical: the collar's financing leg on a 1,200 share book is
    twelve contracts at 540, which reads as 648,000 against a 25% cap and is
    refused. The put leg is not, so the cycle bought the expensive half of a
    collar and was denied the half that pays for it.
    """
    if order.is_purchase:
        return order.debit
    opening = opening_contracts(order, portfolio)
    if order.right == "C":
        return Decimal("0")
    return order.strike * opening * SHARES_PER_CONTRACT


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

    # Buying back a short closes at most what is open. Matching on symbol,
    # right, strike and expiry alone let one short contract wave through a
    # purchase of any size -- the rest of which opens a long position at the
    # same strike, past the "buying a call is leverage" refusal below.
    #
    # The directional band happens to catch that today. This check should not
    # need it to: a rule that only holds because a different rule is watching
    # is a rule that stops holding the day the other one is loosened.
    open_short = short_quantity(order, portfolio)
    if open_short:
        if order.contracts <= open_short:
            return Verdict.approve()
        return Verdict.reject(
            f"closing {open_short} short {order.symbol} {order.strike} "
            f"{order.right} but buying {order.contracts}; the surplus opens a "
            "position rather than closing one"
        )

    if order.right == "C":
        return Verdict.reject(
            "buying a call is leverage, not protection; the mandate constrains "
            "losses and cannot be repaired by paying for upside"
        )

    position = portfolio.positions.get(order.symbol)
    if position is None or position.shares <= 0:
        return Verdict.reject(
            f"the portfolio does not hold {order.symbol}, so a put on it is a "
            "directional bet rather than protection"
        )

    # Protection stands behind shares; it does not exceed them. Holding one
    # share authorised any number of puts, and everything past the shares held
    # is a position that pays only if prices fall -- the bet the paragraph
    # above says cannot come from here.
    #
    # Rounded up, because `contracts_to_match` rounds up: a hundred shares are
    # covered by one contract and 101 by two, and refusing the second would
    # refuse a correctly sized hedge over a share.
    covered = -(-position.shares // SHARES_PER_CONTRACT)
    if order.contracts > covered:
        return Verdict.reject(
            f"{order.contracts} puts against {position.shares} shares of "
            f"{order.symbol}; {covered} is all the protection those shares can "
            "stand behind, and the rest is a directional bet"
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

    position = portfolio.positions.get(order.symbol)

    # Selling back a contract the account already owns creates no obligation.
    # The contracts leave the account and there is nothing left to secure, so
    # only the surplus past what is held opens a short and only the surplus is
    # collateralised.
    #
    # This check used to read the whole order. `remedy.closing_orders` prices a
    # handback as a sale of a long put, so releasing 3x SPY 440 asked for
    # 132,000 of free cash to close a position that was already paid for --
    # and a client holding their money in shares does not have it. The release
    # the cycle had computed, journalled and reported as executed was vetoed
    # here for being a naked put it was the opposite of.
    opening = opening_contracts(order, portfolio)
    if opening <= 0:
        return Verdict.approve()

    if order.right == "C":
        held = position.shares if position else 0
        required = opening * SHARES_PER_CONTRACT
        if held < required:
            return Verdict.reject(
                f"naked call: {held} shares of {order.symbol} held, "
                f"{required} required to cover"
            )
        return Verdict.approve()

    required_cash = order.strike * opening * SHARES_PER_CONTRACT
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
    pct = float(capital_at_risk(order, portfolio) / portfolio.equity * 100)
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
        (portfolio.deployed + capital_at_risk(order, portfolio))
        / portfolio.equity
        * 100
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
