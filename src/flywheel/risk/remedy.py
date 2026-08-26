"""Three ways to close a protection gap, and what each one actually costs.

The ladder says the promise is broken by some number of dollars. This module
says what could be done about it. It does not say which is best, and the
refusal to say so is deliberate.

WHY THERE IS NO SINGLE SCORE
-----------------------------
It is tempting to rank the three on one number and let the agent pick the
winner. That number cannot be built honestly, because the three pay in
different currencies:

- A **protective put** costs premium. Dollars, out of the account, today.
- A **collar** costs little or no premium and instead gives up the gain above
  the call strike. That is an opportunity cost, and it is only realised if the
  market rises.
- **Reducing exposure** costs no premium at all and gives up participation in
  every future gain on the shares sold, forever, along with whatever the sale
  realises.

Putting a certain $2,400 and a contingent "you forgo gains above 800" on the
same axis requires a view on how likely the market is to rise and by how much.
This project does not have one and will not pretend to. So all three are
computed, all three are reported with their price stated in their own units,
and the mandate names the order it prefers. A preference stated in advance by
the client is a policy; a score invented by the agent is a forecast wearing a
lab coat.

WHY `gap_after` IS RECOMPUTED, NOT ESTIMATED
---------------------------------------------
Every remedy reports the gap it would leave by building the proposed position
and running the same `ladder()` over it. Nothing here approximates a payoff. If
a remedy claims to close the gap, the arithmetic that says so is the arithmetic
the mandate is measured with, and a judge can check it by hand.

THE COVERED-CALL RULE HAS NO EXCEPTION HERE EITHER
---------------------------------------------------
A collar sells a call. `forbid_naked` requires a hundred shares behind every
one, and this module counts the shares actually held rather than assuming the
underlying is there. The optimizer once proposed four contracts against a
hundred shares and the risk gate refused it at every expiry for two and a half
years; that bug is not being reintroduced in a new file.

THE FOURTH ACTION: GIVING PROTECTION BACK
------------------------------------------
Buying is only half of it. Protection that is no longer holding the promise up
is a standing charge against the client, and an agent that only ever bought
would accumulate a sediment of hedges against positions closed months ago.
`release` is the symmetric operation, and it is governed by two rules that are
easy to get wrong:

- **Two thresholds, not one.** Protection is bought at the line and given back
  only with headroom inside it. A single threshold produces an agent that buys
  at the boundary, sells at the boundary, and pays the spread twice to stand
  still.
- **Never on the hedge's own profit.** A put is worth most exactly when it is
  needed most. An agent that took profits on protection would be an agent that
  de-hedges into a decline, which is why nothing in `release` reads a price.
  The ladder decides; the mark is not consulted and is not even passed in.
"""

import math
from dataclasses import dataclass, replace
from decimal import Decimal

from flywheel.domain import SHARES_PER_CONTRACT
from flywheel.risk.stress import DEFAULT_SHOCKS, Holding, OptionLeg, gap_at, ladder

# A remedy that leaves less than this much of the gap open is treated as having
# closed it. Contracts are lumpy — a hundred shares at a time — so demanding an
# exact zero would reject a position that overshoots by eleven dollars.
CLOSED_ENOUGH = 500.0

# The three, in no order. The order is the client's and lives in the mandate:
# ranking a certain premium against a contingent ceiling needs a view on the
# market, and a view the agent invented would be a forecast with a policy label.
KINDS: tuple[str, ...] = ("protective_put", "collar", "reduce_exposure")


@dataclass(frozen=True)
class Remedy:
    """One way to close the gap, priced in its own currency.

    `premium_cost` and `forgone_upside` are never added together. Both may be
    non-zero, and the reader is trusted to know that a certain cost and a
    contingent one are different things.
    """

    kind: str  # "protective_put" | "collar" | "reduce_exposure"
    describe: str
    legs: list[OptionLeg]
    shares_sold: dict[str, int]
    # Dollars leaving the account today, certain. Negative when the position
    # is opened for a credit -- a collar whose call outsells its put pays the
    # client to be protected, and clamping that to zero would hide it.
    premium_cost: float
    forgone_upside: float  # dollars of gain given up IF the market rises
    upside_measured_at: float  # the up-move `forgone_upside` is quoted at
    gap_before: float
    gap_after: float

    @property
    def closes_the_gap(self) -> bool:
        return self.gap_after <= CLOSED_ENOUGH

    def line(self) -> str:
        """One row for the journal and the status page."""
        cost = (
            f"{-self.premium_cost:,.0f} credit"
            if self.premium_cost < 0
            else f"{self.premium_cost:,.0f} premium"
        )
        if self.forgone_upside:
            cost += (
                f", {self.forgone_upside:,.0f} of upside above "
                f"+{self.upside_measured_at * 100:.0f}%"
            )
        return (
            f"{self.kind:<17} {self.describe:<44} "
            f"gap {self.gap_before:,.0f} -> {self.gap_after:,.0f}   {cost}"
        )


def _gap(holdings, legs, budget, shock) -> float:
    rung = gap_at(ladder(holdings, legs, budget), shock)
    return rung.gap if rung else 0.0


def _slack(holdings, legs, budget, shock) -> float:
    """Dollars of headroom under the budget at the promised shock.

    The same number as `gap`, read from the other side and allowed to be
    positive. `gap` clamps at zero because a portfolio inside its budget has no
    shortfall to close; releasing protection needs to know *how far* inside,
    which is the part `gap` throws away.
    """
    rung = gap_at(ladder(holdings, legs, budget), shock)
    if rung is None:
        return 0.0
    return rung.budget + rung.portfolio_loss  # loss is negative


def _is_protection(leg: OptionLeg) -> bool:
    """A long put. The only thing in this book that pays when the market falls."""
    return leg.right == "P" and leg.contracts > 0


def _protection_at(leg: OptionLeg, shock: float) -> float:
    """What this leg contributes at `shock`, above where it sits flat.

    Measured the way `ladder` measures it, against the zero-shock baseline, so
    the premium already paid does not count as protection. A put bought for
    9.00 that expires worthless protected nothing; it merely cost 9.00.
    """
    return leg.pnl_at(shock) - leg.pnl_at(0.0)


def _without(legs: list[OptionLeg], plan: dict[int, int]) -> list[OptionLeg]:
    """The book that remains after releasing `plan` contracts from each leg."""
    remaining = []
    for index, leg in enumerate(legs):
        left = leg.contracts - plan.get(index, 0)
        if left:
            remaining.append(replace(leg, contracts=left))
    return remaining


def _contracts_needed(strike: Decimal, spot: float, shock: float, gap: float) -> int:
    """How many puts it takes to cover `gap` dollars at the promised shock.

    Sized on intrinsic value at the shock, ignoring the premium, because the
    premium reduces the payout at every price equally and is reported
    separately as the cost. Rounded up: a remedy that closes most of a gap has
    not closed it.
    """
    terminal = spot * (1 + shock)
    intrinsic = max(float(strike) - terminal, 0.0)
    if intrinsic <= 0:
        return 0
    return math.ceil(gap / (intrinsic * SHARES_PER_CONTRACT))


def protective_put(
    holdings: list[Holding],
    legs: list[OptionLeg],
    budget: float,
    shock: float,
    symbol: str,
    spot: float,
    puts: list[dict],
) -> Remedy | None:
    """Buy protection outright. Costs money, gives up nothing else.

    The strike chosen is the cheapest one that closes the gap, not the highest
    or the furthest out. A nearer strike protects more per contract and costs
    more per contract; which combination is cheapest in total is an arithmetic
    question with an answer today, and it is answered rather than guessed.
    """
    gap = _gap(holdings, legs, budget, shock)
    if gap <= 0:
        return None

    best: Remedy | None = None
    for row in puts:
        ask = float(row.get("ask") or 0)
        if ask <= 0:
            continue
        strike = Decimal(str(row["strike"]))
        count = _contracts_needed(strike, spot, shock, gap)
        if count <= 0:
            continue
        cost = ask * count * SHARES_PER_CONTRACT
        leg = OptionLeg(symbol, "P", strike, count, Decimal(str(ask)), spot)
        after = _gap(holdings, [*legs, leg], budget, shock)
        if after > CLOSED_ENOUGH:
            continue
        candidate = Remedy(
            kind="protective_put",
            describe=f"buy {count}x {symbol} {strike} put at {ask:.2f}",
            legs=[leg],
            shares_sold={},
            premium_cost=cost,
            forgone_upside=0.0,
            upside_measured_at=0.0,
            gap_before=gap,
            gap_after=after,
        )
        if best is None or candidate.premium_cost < best.premium_cost:
            best = candidate
    return best


def collar(
    holdings: list[Holding],
    legs: list[OptionLeg],
    budget: float,
    shock: float,
    symbol: str,
    spot: float,
    puts: list[dict],
    calls: list[dict],
    up_move: float = 0.10,
) -> Remedy | None:
    """Pay for the put by selling a call. Cheap in cash, expensive in ceiling.

    The number of calls sold never exceeds the shares held divided by a
    hundred, and it never exceeds the number of puts bought. The first rule is
    `forbid_naked`; the second keeps the position a collar rather than a
    covered-call trade with a put attached.

    `forgone_upside` is quoted at a stated up-move rather than left abstract.
    "You give up gains above 800" means nothing without knowing where the stock
    is; "at +10% this costs you 4,200" is a number the client can weigh against
    a premium.

    It is a reading at one point, not a summary of the payoff. A call struck
    above the measured move reports zero, which is true at that move and false
    beyond it — `upside_measured_at` travels with the number so the reader can
    see which question was asked.
    """
    protection = protective_put(holdings, legs, budget, shock, symbol, spot, puts)
    if protection is None:
        return None

    shares = sum(h.shares for h in holdings if h.symbol == symbol and h.shocked)
    coverable = shares // SHARES_PER_CONTRACT
    wanted = protection.legs[0].contracts
    count = min(coverable, wanted)
    if count <= 0:
        return None

    # Only calls above spot: selling a call already in the money is not
    # financing protection, it is agreeing to sell the shares at a loss.
    above = [
        row
        for row in calls
        if float(row.get("bid") or 0) > 0 and float(row["strike"]) > spot
    ]
    if not above:
        return None

    put_leg = protection.legs[0]
    target = protection.premium_cost / (count * SHARES_PER_CONTRACT)
    # The highest strike whose bid still pays for the put. A higher strike
    # gives up less upside, so among the calls that fund the protection the
    # highest one is strictly better for the client.
    funding = [row for row in above if float(row["bid"]) >= target]
    row = (
        max(funding, key=lambda r: float(r["strike"]))
        if funding
        else max(above, key=lambda r: float(r["bid"]))
    )

    bid = float(row["bid"])
    strike = Decimal(str(row["strike"]))
    call_leg = OptionLeg(symbol, "C", strike, -count, Decimal(str(bid)), spot)
    received = bid * count * SHARES_PER_CONTRACT
    after = _gap(holdings, [*legs, put_leg, call_leg], budget, shock)

    terminal = spot * (1 + up_move)
    forgone = max(terminal - float(strike), 0.0) * count * SHARES_PER_CONTRACT
    return Remedy(
        kind="collar",
        describe=(
            f"buy {put_leg.contracts}x {put_leg.strike} put, "
            f"sell {count}x {strike} call at {bid:.2f}"
        ),
        legs=[put_leg, call_leg],
        shares_sold={},
        premium_cost=protection.premium_cost - received,
        forgone_upside=forgone,
        upside_measured_at=up_move,
        gap_before=protection.gap_before,
        gap_after=after,
    )


def reduce_exposure(
    holdings: list[Holding],
    legs: list[OptionLeg],
    budget: float,
    shock: float,
    symbol: str,
) -> Remedy | None:
    """Sell shares until the promise holds. No premium, no ceiling, no upside.

    The cheapest remedy to buy and the most expensive to live with. It is
    listed alongside the other two rather than treated as the fallback, because
    on a day when protection is expensive it is often the honest answer -- and
    an agent that only ever offered to buy something would be an agent with a
    reason to find protection affordable.

    `forgone_upside` here is unbounded in principle and is quoted the same way
    as the collar's, at a stated up-move, so the three rows compare.
    """
    gap = _gap(holdings, legs, budget, shock)
    if gap <= 0:
        return None

    holding = next(
        (h for h in holdings if h.symbol == symbol and h.shocked and h.shares > 0), None
    )
    if holding is None:
        return None

    # Each share sold removes `price * |shock|` from the loss at the promised
    # shock. Rounded up, then capped at what is actually held.
    per_share = holding.price * abs(shock)
    if per_share <= 0:
        return None
    sell = min(holding.shares, math.ceil(gap / per_share))

    reduced = [
        Holding(h.symbol, h.shares - sell, h.price, h.shocked) if h is holding else h
        for h in holdings
    ]
    # The proceeds do not vanish; they sit in cash, which does not move.
    reduced.append(Holding("CASH", int(sell * holding.price), 1.0, shocked=False))
    after = _gap(reduced, legs, budget, shock)
    return Remedy(
        kind="reduce_exposure",
        describe=f"sell {sell} shares of {symbol} at {holding.price:.2f}",
        legs=[],
        shares_sold={symbol: sell},
        premium_cost=0.0,
        forgone_upside=sell * holding.price * 0.10,
        upside_measured_at=0.10,
        gap_before=gap,
        gap_after=after,
    )


# --- giving protection back -------------------------------------------------


@dataclass(frozen=True)
class Release:
    """Protection to hand back, and what handing it back gives up.

    Deliberately not a `Remedy`. A remedy is priced, because the client is
    choosing what to buy and the cost is the choice. A release carries no price
    at all, and the absence is the design: see `release` for why the mark is
    never consulted.
    """

    legs: list[OptionLeg]  # contracts to close, positive, as held
    # The book that is left once these are gone. Carried rather than re-derived
    # because the caller's next question is always "and what do I still need?",
    # and answering it from the released list means reconstructing the plan.
    kept: list[OptionLeg]
    reason: str  # "spent" | "redundant" | "spent and redundant"
    describe: str
    slack_before: float
    slack_after: float
    margin_required: float
    # Protection at the deepest rung that goes away with these legs. The agent
    # promises one shock and discloses the rest; releasing a leg that was still
    # worth something in the tail is allowed, but it is not allowed to be quiet.
    tail_given_up: float
    tail_shock: float
    # Symbols left holding a short call with no long put behind it any more.
    # Not blocked, because the broker reports positions and not intentions --
    # see `release`.
    leaves_ceiling: list[str]

    @property
    def contracts(self) -> int:
        return sum(leg.contracts for leg in self.legs)

    def line(self) -> str:
        """One row for the journal and the status page."""
        note = ""
        if self.tail_given_up:
            note = (
                f", gives up {self.tail_given_up:,.0f} at "
                f"{self.tail_shock * 100:.0f}%"
            )
        if self.leaves_ceiling:
            note += f", ceiling left standing on {', '.join(self.leaves_ceiling)}"
        return (
            f"{'release':<17} {self.describe:<44} "
            f"headroom {self.slack_before:,.0f} -> {self.slack_after:,.0f}"
            f"   {self.reason}{note}"
        )


def release(
    holdings: list[Holding],
    legs: list[OptionLeg],
    budget: float,
    shock: float,
    margin_pct: float = 15.0,
) -> Release | None:
    """Protection the book no longer needs, in the two senses that differ.

    **Spent** protection pays nothing at the promised shock. A put struck at 440
    protects a 500 stock at -20%; after a rally to 550 the shocked price is 440
    itself and the same put pays zero. It is not protecting the promise, so
    releasing it cannot widen the gap — by definition, removing something worth
    nothing at that rung leaves the rung where it was. This is what makes a roll
    possible: the dead leg goes, and `protective_put` buys a live one against
    the gap that remains. Without it the agent would buy a fresh put every rally
    and stack the corpses.

    **Redundant** protection still pays, but the promise holds without it and
    with room to spare. This is the release that needs a margin, and the margin
    is why there are two thresholds rather than one. Protection is bought the
    moment the gap opens; it is given back only once the book is `margin_pct` of
    the budget clear of the line. An agent using one threshold for both would
    buy at the boundary, sell at the boundary, and pay the spread twice for the
    privilege of ending where it started.

    WHY A RALLY DOES NOT RELEASE ANYTHING
    --------------------------------------
    The intuition that a rising market makes protection unnecessary is exactly
    backwards here, and the arithmetic says so. The budget is a percentage of
    equity and the exposure is the thing that grew: a 10% rally on a 600,000
    sleeve takes the loss at -20% from 120,000 to 132,000 while the budget goes
    from 100,000 to 106,000. The gap widens by a third. What a rally releases is
    not the need for protection but the specific strike that used to supply it.

    WHY NOTHING HERE READS A PRICE
    -------------------------------
    A put is worth most precisely when it is needed most. An agent that released
    protection because the position showed a profit would sell the hedge into
    the decline it was bought for. So the decision is made entirely on the
    ladder, and the mark is not passed to this function -- not weighed and
    rejected, but absent, so no future edit can quietly start consulting it.

    WHAT IS DISCLOSED RATHER THAN BLOCKED
    --------------------------------------
    If every long put on a symbol is released while a short call on it remains,
    the client keeps the ceiling and loses the floor. That is the worst half of
    a collar, and it would be right to refuse -- except that a short call may
    equally be the wheel's own covered call, which is a legitimate position that
    stands on its own. The broker reports positions, not intentions, and the
    difference is not recoverable from a list of holdings. So it is reported in
    `leaves_ceiling` and left to the caller, rather than guessed at here.
    """
    protective = [i for i, leg in enumerate(legs) if _is_protection(leg)]
    if not protective:
        return None

    slack_before = _slack(holdings, legs, budget, shock)
    required = budget * margin_pct / 100

    # Spent first, and unconditionally: a leg worth nothing at the promised
    # shock is not what is holding the promise up, so the margin has no say.
    plan = {
        i: legs[i].contracts
        for i in protective
        if _protection_at(legs[i], shock) <= 0
    }
    spent = bool(plan)

    # Then the redundant ones, weakest first. Ordering by protection per
    # contract ascending releases as many contracts as the margin permits: the
    # legs that do the least work go first, and the ones actually carrying the
    # promise stay.
    rest = sorted(
        (i for i in protective if i not in plan),
        key=lambda i: _protection_at(legs[i], shock) / legs[i].contracts,
    )
    redundant = False
    for index in rest:
        for count in range(legs[index].contracts, 0, -1):
            kept = _without(legs, {**plan, index: count})
            if _slack(holdings, kept, budget, shock) >= required:
                plan[index] = count
                redundant = True
                break

    if not plan:
        return None

    released = [replace(legs[i], contracts=count) for i, count in sorted(plan.items())]
    remaining = _without(legs, plan)
    tail = DEFAULT_SHOCKS[-1]

    still_protected = {leg.symbol for leg in remaining if _is_protection(leg)}
    orphaned = {
        leg.symbol
        for leg in remaining
        if leg.right == "C"
        and leg.contracts < 0
        and leg.symbol not in still_protected
    }
    leaves_ceiling = sorted(orphaned & {leg.symbol for leg in released})

    if spent and redundant:
        reason = "spent and redundant"
    else:
        reason = "spent" if spent else "redundant"
    return Release(
        legs=released,
        kept=remaining,
        reason=reason,
        describe=", ".join(
            f"close {leg.contracts}x {leg.symbol} {leg.strike} put" for leg in released
        ),
        slack_before=slack_before,
        slack_after=_slack(holdings, remaining, budget, shock),
        margin_required=required,
        tail_given_up=sum(_protection_at(leg, tail) for leg in released),
        tail_shock=tail,
        leaves_ceiling=leaves_ceiling,
    )
