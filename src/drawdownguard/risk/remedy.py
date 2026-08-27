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

There is a sharper reason than fastidiousness. The obvious score is cash, and
cash is the one axis on which selling shares always wins: it costs nothing to
sell. An agent ranking on cost would therefore liquidate the portfolio one
breach at a time and report an excellent cost record while doing it. The score
would not be merely unfounded, it would be reliably wrong in one direction.

WHAT *CAN* BE COMPARED, AND IS
------------------------------
Within one currency the arithmetic is honest and gets done. `cash_per_1k`
prices the two option remedies against each other -- dollars of premium per
thousand dollars of gap closed -- and it is a fact, not a view. It is `None`
for anything that costs no cash, so a remedy that is not competing on that axis
cannot win on it by scoring zero. Across currencies there is nothing to
compute, and `permanent` is the reason: a put expires and the book returns to
where it was, while a share sold does not come back.

HOW THE CHOICE IS MADE WITHOUT A FORECAST
------------------------------------------
Once the permanent remedy is set aside, the field narrows to two, and they are
not two philosophies. A collar *is* the protective put, plus one extra trade:
selling a call. So the agent never has to answer "do I prefer cash or ceiling
in the abstract." It only has to answer "is selling this particular call, at
today's price, a good trade" -- and the chain answers that itself.

The criterion is relative value, not direction. A call sold at an implied
volatility at or above the put's is protection financed on fair terms: the
client parts with something the market prices at least as dearly, per unit of
risk, as the thing being bought. A call sold below the put's volatility is the
reverse trade -- surrendering cheap upside to buy expensive downside -- and it
is the worse deal at every outcome, not merely at the ones a forecast would
favour. No view on where the market goes is needed to see that, and none is
taken. See `choose`.

Two consequences worth stating in advance rather than discovering. On equity
indices the skew usually puts put volatility above call volatility, so this
rule will usually buy the bare put; that is the skew being real, not the rule
failing, and the days it flips are the days it earns its keep. And when either
volatility is missing the rule declines to sell -- an unmeasurable trade in the
client's upside is not one to take on the grounds that it looked cheap.

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
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from drawdownguard.domain import SHARES_PER_CONTRACT, ProposedOrder
from drawdownguard.optimizer.payoff import assignment_prob, bs_delta, contract_vega
from drawdownguard.risk.stress import (
    DEFAULT_SHOCKS,
    Holding,
    OptionLeg,
    gap_at,
    ladder,
    worst_loss,
)

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
    # The two volatilities the financing decision turns on: what the client is
    # buying, and what the client is selling to pay for it. Both None on a
    # remedy that sells nothing. Either None when the chain did not supply it,
    # which `financed_fairly` reads as "do not sell" rather than as "proceed".
    # The same position again, in the shape that can actually be sent. `legs`
    # carry a strike and a premium -- enough to stress the payoff, not enough
    # to trade -- while these carry the expiry and the greeks the risk gate
    # reads. Empty on `reduce_exposure`, which sells shares and files no option
    # order at all.
    #
    # Built where the chain row is in hand rather than looked up again later:
    # a second read of the chain can return a different quote, and an order
    # priced off a row nobody journalled is an order nobody can check.
    orders: tuple[ProposedOrder, ...] = field(default_factory=tuple)
    protection_iv: float | None = None
    financing_iv: float | None = None
    # Dollars the sold call brings in, and how far above spot the ceiling sits,
    # in percent. Kept apart from `premium_cost` so the terms of the financing
    # stay legible after the netting.
    financing_credit: float = 0.0
    ceiling_pct: float = 0.0

    @property
    def closes_the_gap(self) -> bool:
        return self.gap_after <= CLOSED_ENOUGH

    @property
    def gap_closed(self) -> float:
        """Dollars of shortfall this removes. Never negative."""
        return max(self.gap_before - self.gap_after, 0.0)

    @property
    def permanent(self) -> bool:
        """True when the remedy has no expiry and cannot be undone.

        Keyed on shares actually sold rather than on `kind`, because it is a
        fact about the position and not about the label. A put and a collar both
        expire and hand the book back unchanged; a share sold is a decision the
        client cannot reverse by waiting, and the only way back is to buy it
        again at whatever it then costs.

        This is the field that stops a temporary breach from being answered
        permanently. A gap opened by a 3% move can close on a 3% move back, and
        a three-week put is sized to exactly that kind of cause.
        """
        return bool(self.shares_sold)

    @property
    def cash_per_1k(self) -> float | None:
        """Premium per 1,000 dollars of gap closed, or None if it costs no cash.

        The one comparison this module is willing to make, because both sides
        of it are dollars leaving the account today. `None` rather than zero for
        a credit or a share sale: zero would read as "free" and win a ranking it
        is not standing in. A remedy that costs no cash is not cheap, it is
        priced in something else, and the something else is on the next line.
        """
        if self.premium_cost <= 0 or self.gap_closed <= 0:
            return None
        return self.premium_cost * 1000 / self.gap_closed

    @property
    def upside_price(self) -> float | None:
        """Dollars collected per 1% of upside surrendered.

        The terms of the financing in one number, and the one that actually
        moves day to day: it rises when calls are dear and falls when they are
        cheap. Reported rather than thresholded -- a cutoff would be a constant
        somebody picked, and `financed_fairly` answers the same question against
        the market instead of against a constant.
        """
        if self.ceiling_pct <= 0 or self.financing_credit <= 0:
            return None
        return self.financing_credit / self.ceiling_pct

    @property
    def financed_fairly(self) -> bool | None:
        """Is the call being sold at least as dearly as the put being bought?

        Implied volatility is the comparison because it is the price per unit
        of risk, which is what makes two different strikes comparable at all.
        Raw premium cannot do this job: a near call costs more than a far put
        for reasons that have nothing to do with which is the better sale.

        None when either volatility is missing, and callers must not read that
        as a yes. The chain not saying what something is worth is not evidence
        that selling it is a good idea.
        """
        if self.protection_iv is None or self.financing_iv is None:
            return None
        return self.financing_iv >= self.protection_iv

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

    Superseded by `contracts_to_match`, and kept only for the collar path until
    that is converted too. Two things are wrong with sizing this way, and both
    are load-bearing:

    - it answers about one price. A hedge adequate at exactly the shock being
      tested can be worthless a few percent above it, and this picks the
      cheapest structure that passes whichever test is being run.
    - it excludes the premium it is about to spend. That money comes out of the
      same account the promise is written against, so the hedge comes up short
      by exactly what it cost.
    """
    terminal = spot * (1 + shock)
    intrinsic = max(float(strike) - terminal, 0.0)
    if intrinsic <= 0:
        return 0
    return math.ceil(gap / (intrinsic * SHARES_PER_CONTRACT))


def liquid(rows: list[dict], limits) -> list[dict]:
    """The rows that are choices at all, by the gate's own two liquidity rules.

    Filtered here rather than left for the gate, because the gate is the last
    word and by then it is too late to pick differently. A solver that reaches
    for the cheapest strike on the whole chain will find the one nobody trades:
    an illiquid contract is cheap in the way an unrepeatable price is cheap.

    Found by running a live cycle. The agent measured the promise, found the
    gap, solved for a SPY 644 put, sent it, and was refused for an open interest
    of 209 against a floor of 500 -- leaving the gap open and nothing on the way
    to close it. Every step was individually right and the cycle still did
    nothing, which is the failure mode worth designing against.

    A row missing either field is dropped. Absent liquidity is not liquidity.
    """
    out = []
    for row in rows:
        bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
        if bid <= 0 or ask <= 0:
            continue
        if int(row.get("open_interest") or 0) < limits.min_open_interest:
            continue
        mid = (bid + ask) / 2
        if mid <= 0 or (ask - bid) / mid * 100 > limits.max_spread_pct:
            continue
        out.append(row)
    return out


def _tradable(row: dict) -> bool:
    """Whether a chain row carries what an order needs.

    The rows the live chain returns always do. The rows the tests build are
    deliberately thin -- a strike and a price, which is all a payoff needs --
    and a remedy that could not be priced without an expiry would force every
    arithmetic test to invent one. So a thin row still produces a remedy, with
    no order attached, and `execute` finds nothing to send rather than sending
    something malformed.
    """
    return bool(row.get("expiry")) and bool(row.get("implied_vol"))


def order_for(symbol: str, row: dict, contracts: int, spot: float) -> ProposedOrder:
    """One chain row as an order the gate can rule on and the broker can fill.

    A `Remedy` describes a position in `OptionLeg`s, which carry a strike and a
    premium and nothing else -- enough to stress the payoff, and not enough to
    trade. Expiry and the greeks come back off the chain row here, so a remedy
    arrives at `execute` already in the shape the risk gate reads.

    The greeks are recomputed from the row's own implied volatility rather than
    taken from a vendor field, because `contract_vega` is the project's
    convention and three incompatible ones are in circulation. `limit_price` is
    the ask on a purchase and the bid on a sale: the side being crossed to,
    never the mid, which is a price nobody is offering.
    """
    tau = max((row["expiry"] - date.today()).days, 0) / 365.0
    strike, vol = float(row["strike"]), float(row["implied_vol"])
    buying = contracts > 0
    price = row["ask"] if buying else row["bid"]
    bid, ask = float(row["bid"]), float(row["ask"])
    mid = (bid + ask) / 2
    return ProposedOrder(
        symbol=symbol,
        right=row["right"],
        strike=Decimal(str(row["strike"])),
        expiry=row["expiry"],
        contracts=contracts,
        limit_price=Decimal(str(price)),
        delta=bs_delta(spot, strike, tau, vol, row["right"]),
        vega=contract_vega(spot, strike, tau, vol),
        assignment_prob=assignment_prob(spot, strike, tau, vol, row["right"]),
        open_interest=int(row.get("open_interest") or 0),
        spread_pct=((ask - bid) / mid * 100) if mid > 0 else 100.0,
        spot=spot,
    )


def contracts_to_match(holdings: list[Holding], spot: float) -> int:
    """Enough puts to stand behind every share the client owns.

    Any fewer and there is no floor at all. Below the strike the covered shares
    stop losing, but the uncovered ones carry on down, so the worst case runs
    away again -- a hedge over half the book is not half a promise kept, it is
    no promise kept with half the bill.

    The whole shocked book is counted, not just the hedge instrument's own
    shares. A diversified equity portfolio is hedged with index puts because
    that is what a liquid chain exists for, and `risk/concentration.py` measured
    these ETFs correlating 0.78 to 0.95 with one another. That is the assumption
    being made and it is worth naming: in a shock where the client's holdings
    part company with the index, the floor is approximate rather than exact.

    Rounded up. The leftover fraction of a contract protects slightly more than
    the client owns, which errs toward the promise rather than away from it.
    """
    exposure = sum(h.value for h in holdings if h.shocked)
    if exposure <= 0 or spot <= 0:
        return 0
    return math.ceil(exposure / (spot * SHARES_PER_CONTRACT))


def solve_for_strike(
    holdings: list[Holding],
    legs: list[OptionLeg],
    budget: float,
    symbol: str,
    spot: float,
    puts: list[dict],
) -> tuple[Decimal, int, float] | None:
    """The cheapest put that keeps the promise at every depth, or None.

    Returns `(strike, contracts, ask)`.

    Nobody names a shock here. Matched puts turn the worst case into "the fall
    down to the strike, plus what the protection cost", and the answer is the
    lowest strike where those two still fit inside the budget:

    - lower, and the market has too far to travel before the put engages. The
      unprotected drop alone spends the budget.
    - higher, and the promise holds with room to spare -- room the client paid
      for and never asked for. Protection is a cost, and a dollar past the
      promise is a dollar taken for nothing.

    The two terms move against each other, so the total is monotonic in the
    strike and the answer is unique. There is no ranking, no tie-break and no
    preference: the chain decides, and it decides the same way for anyone who
    checks.
    """
    contracts = contracts_to_match(holdings, spot)
    if contracts <= 0:
        return None

    best: tuple[Decimal, int, float] | None = None
    for row in sorted(puts, key=lambda r: float(r["strike"])):
        ask = float(row.get("ask") or 0)
        if ask <= 0:
            continue
        strike = Decimal(str(row["strike"]))
        candidate = OptionLeg(symbol, "P", strike, contracts, Decimal(str(ask)), spot)
        cost = ask * contracts * SHARES_PER_CONTRACT
        if worst_loss(holdings, [*legs, candidate], cost) <= budget:
            best = (strike, contracts, ask)
            break  # sorted ascending, so the first that fits is the cheapest
    return best


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

    The strike comes from `solve_for_strike`, which asks the whole descent
    rather than one price on it. Contracts match the shares, so below the
    strike the loss stops falling and the client's worst case is the drop down
    to the strike plus the premium -- the lowest strike where those two still
    fit the budget is the answer, and it is unique.

    `gap_before` and `gap_after` still quote the mandate's own shock, because
    that is the number the journal and the status page report and a reader can
    check by hand. They describe the outcome; they no longer choose it.
    """
    gap = _gap(holdings, legs, budget, shock)
    if gap <= 0:
        return None

    solved = solve_for_strike(holdings, legs, budget, symbol, spot, puts)
    if solved is None:
        return None
    strike, count, ask = solved

    leg = OptionLeg(symbol, "P", strike, count, Decimal(str(ask)), spot)
    cost = ask * count * SHARES_PER_CONTRACT
    row = next(r for r in puts if Decimal(str(r["strike"])) == strike)
    return Remedy(
        kind="protective_put",
        describe=f"buy {count}x {symbol} {strike} put at {ask:.2f}",
        legs=[leg],
        shares_sold={},
        premium_cost=cost,
        forgone_upside=0.0,
        upside_measured_at=0.0,
        gap_before=gap,
        gap_after=_gap(holdings, [*legs, leg], budget, shock),
        orders=(order_for(symbol, row, count, spot),) if _tradable(row) else (),
        protection_iv=row.get("implied_vol"),
    )


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
        protection_iv=protection.protection_iv,
        financing_iv=row.get("implied_vol"),
        financing_credit=received,
        ceiling_pct=(float(strike) - spot) / spot * 100 if spot > 0 else 0.0,
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


# --- choosing between them --------------------------------------------------


def choose(offers: list[Remedy]) -> tuple[Remedy | None, str]:
    """The remedy today's prices favour, and the sentence explaining why.

    The reason is returned rather than logged here because a decision whose
    justification is written somewhere else can drift from it. They are one
    value, and the journal records the value.

    The order of the questions is the design:

    1. **Does it close the gap?** A remedy that leaves the promise broken is not
       a cheaper way of keeping it.
    2. **Is it reversible?** Everything that expires is preferred to anything
       that does not, and no price improves a one-way door enough to reorder
       this. Selling shares answers a condition that is usually temporary with a
       decision that is permanent, so it is reached for only when nothing else
       on the chain closes the gap at all.
    3. **Are the financing terms fair?** This is the only step where prices
       decide, and it compares like with like: the volatility sold against the
       volatility bought. Everything else is settled before any number from the
       chain is consulted.

    There is no client-stated ranking here and there used to be. It was
    `protection_order` in the mandate, and it was wrong for a reason worth
    keeping: it gave the same answer on every day of every market, so an agent
    reading it was not choosing, it was replaying a config file. What a client
    can usefully state in advance is a constraint -- how much may be lost, what
    may never be sold. Which of two option structures is better value this
    Tuesday is not a preference, it is an observation, and the chain makes it.
    """
    closing = [remedy for remedy in offers if remedy.closes_the_gap]
    if not closing:
        return None, "nothing on today's chain closes the gap"

    reversible = [remedy for remedy in closing if not remedy.permanent]
    if not reversible:
        return (
            min(closing, key=lambda r: r.premium_cost),
            "no remedy that expires closes the gap today, so the only one left "
            "is permanent",
        )

    ring = next((r for r in reversible if r.kind == "collar"), None)
    put = next((r for r in reversible if r.kind == "protective_put"), None)
    if ring is None or put is None:
        only = ring or put or min(reversible, key=lambda r: r.premium_cost)
        return only, f"the only remedy that closes the gap today is a {only.kind}"

    fair = ring.financed_fairly
    if fair is None:
        return put, (
            "bought outright: the chain did not price both legs, and upside is "
            "not sold on terms that cannot be checked"
        )
    saved = put.premium_cost - ring.premium_cost
    terms = (
        f"call at {ring.financing_iv:.1%} vol against a put at "
        f"{ring.protection_iv:.1%}"
    )
    if fair:
        return ring, (
            f"financed by selling the richer leg -- {terms} -- saving "
            f"{saved:,.0f} for a ceiling {ring.ceiling_pct:.1f}% above spot"
        )
    return put, (
        f"bought outright: the call would be sold below the put's price per "
        f"unit of risk -- {terms} -- so the {saved:,.0f} saved would come out "
        f"of underpriced upside"
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
