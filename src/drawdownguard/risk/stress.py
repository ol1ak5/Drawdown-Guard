"""What the portfolio loses if the market falls, and whether that breaks the promise.

The client states a downside budget once, calmly, in advance: *no more than 10%
in a market shock*. The portfolio never hears about it. Exposure drifts,
options expire, hedges decay, and the promise quietly stops being true. Nobody
finds out until the market tests it.

This module is how the agent finds out first.

NOT A FORECAST
--------------
"If a 20% shock happened, here is where you would be" is a statement about the
positions held today. It contains no view on whether a shock will happen, when,
or how likely it is. That distinction is the reason this belongs in a project
that refuses to predict: the ladder is arithmetic on the current book, and a
judge can check every row by hand.

The shocks are not equally likely and the module does not pretend otherwise.
-35% is on the ladder because the CBOE PUT index — the published record of this
exact strategy class — lost 37% in 2008. It is on the ladder as history, not as
a probability.

THE PROMISE IS AN INTERVAL, NOT A POINT
----------------------------------------
A mandate that promises against a 20% shock has not promised only about the
single price 20% below today. It has promised that the client does not lose
more than the budget *anywhere on the way down to it*. Those are different
claims, and with options they come apart.

Shares alone cannot tell them apart: the loss grows monotonically, so the
deepest point of the interval is also the worst one, and checking the endpoint
is checking everything. Add options and that stops being true. The payoff bends
at every strike, and a hedge sized to be exactly adequate at the endpoint can
be inadequate a few percent above it -- protection bought so close to the
promised shock that it is nearly worthless just short of it. Sizing against one
point rewards exactly that hedge: it is the cheapest thing that passes the test
being run.

`gap_within` checks the interval instead, and it does so exactly rather than by
sampling. See `bends` for why no step size is needed.

SIGN CONVENTIONS, WHICH ARE WHERE THE BUGS LIVE
------------------------------------------------
Every position is valued at the shocked price and compared to today, so a
"loss" is negative throughout.

- **Shares**: move with the shock, one for one.
- **Short call**: the premium is already collected. Above the strike the
  obligation bites, and the position gives back the intrinsic value.
- **Long put**: the premium is already paid. Below the strike it pays intrinsic
  value, which is the whole point of owning it.
- **Short put**: the premium is collected, and below the strike the loss grows
  without a floor until the strike reaches zero.
- **Cash and bills**: unmoved. Short-duration Treasuries barely respond to an
  equity shock, and this module treats them as flat rather than pretending they
  hedge. In 2022 long-duration Treasuries fell *with* equities; anything that
  calls a bond a hedge learns the correlation at the worst possible moment.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from drawdownguard.domain import SHARES_PER_CONTRACT

# The scenarios the mandate is checked against. Fixed, published, and the same
# every day: a ladder that moved with the market would let a bad day redefine
# what "safe" means.
DEFAULT_SHOCKS: tuple[float, ...] = (-0.05, -0.10, -0.20, -0.35)

# Shocks closer together than this are the same shock. A hundredth of a basis
# point on a 1,000,000 account is a dollar, so nothing real is lost by refusing
# to distinguish them, and `bends` needs it to keep floating-point noise off
# the endpoints.
_EPS = 1e-9


@dataclass(frozen=True)
class Holding:
    """Shares of something, or cash-like with `shocked=False`."""

    symbol: str
    shares: int
    price: float
    shocked: bool = True

    def value_at(self, shock: float) -> float:
        move = shock if self.shocked else 0.0
        return self.shares * self.price * (1 + move)

    @property
    def value(self) -> float:
        return self.shares * self.price


@dataclass(frozen=True)
class OptionLeg:
    """One option position, signed: negative contracts are short."""

    symbol: str
    right: str  # "P" or "C"
    strike: Decimal
    contracts: int  # negative = sold
    premium: Decimal  # per share, paid if long, received if short
    spot: float
    # The day the leg stops existing. None where a caller only needs the
    # payoff -- most of the arithmetic here does, and the tests that check it
    # should not have to invent a date to do so.
    #
    # It is carried because a leg without one cannot be closed. `release`
    # decides which contracts to hand back, and for a while nothing could act
    # on that answer: the journal reported a handback, the puts stayed in the
    # account, and the next cycle bought protection on top of them.
    expiry: date | None = None

    def pnl_at(self, shock: float) -> float:
        """Profit or loss at expiry if the underlying moved by `shock`.

        Valued at expiry rather than marked to model: the ladder asks what the
        book is worth if the shock happens and the position runs its course,
        and an intrinsic-value answer needs no volatility assumption anybody
        could argue with.
        """
        terminal = self.spot * (1 + shock)
        strike = float(self.strike)
        intrinsic = (
            max(strike - terminal, 0.0)
            if self.right == "P"
            else max(terminal - strike, 0.0)
        )
        # Short: keep the premium, owe the intrinsic. Long: paid the premium,
        # receive the intrinsic. `contracts` carries the sign for both.
        per_share = (
            float(self.premium) - intrinsic
            if self.contracts < 0
            else (intrinsic - float(self.premium))
        )
        return per_share * abs(self.contracts) * SHARES_PER_CONTRACT


@dataclass(frozen=True)
class Rung:
    """One row of the ladder."""

    shock: float
    portfolio_loss: float  # negative
    budget: float  # positive, the most the client accepts losing
    protected_by_options: float

    @property
    def shortfall(self) -> float:
        """How far past the promise this scenario takes the portfolio.

        Zero when the mandate holds. Positive is the dollars of protection the
        agent still has to find.
        """
        return max(-self.portfolio_loss - self.budget, 0.0)

    @property
    def breached(self) -> bool:
        return self.shortfall > 0


def ladder(
    holdings: list[Holding],
    options: list[OptionLeg],
    budget: float,
    shocks: tuple[float, ...] = DEFAULT_SHOCKS,
) -> list[Rung]:
    """The stress ladder for the book as it stands right now.

    Rebuilt from the positions that actually exist, every cycle. That is the
    whole mechanism: an option expiring tonight silently widens tomorrow's gap,
    and nothing but recomputation notices.
    """
    rungs = []
    for shock in shocks:
        equity_change = sum(h.value_at(shock) - h.value for h in holdings)
        option_change = sum(leg.pnl_at(shock) for leg in options)
        # Options at zero shock still carry premium already booked; what the
        # ladder wants is the change *caused by the shock*, so the flat case is
        # the baseline.
        option_baseline = sum(leg.pnl_at(0.0) for leg in options)
        protected = option_change - option_baseline
        rungs.append(
            Rung(
                shock=shock,
                portfolio_loss=equity_change + protected,
                budget=budget,
                protected_by_options=protected,
            )
        )
    return rungs


def worst_shortfall(rungs: list[Rung]) -> Rung | None:
    """The rung that breaches the mandate by the most, or None if none do.

    Reported, but not what the agent acts on — see `gap_at`.
    """
    breaches = [r for r in rungs if r.breached]
    return max(breaches, key=lambda r: r.shortfall) if breaches else None


def gap_at(rungs: list[Rung], shock: float) -> Rung | None:
    """The rung at one specific shock: the one the mandate actually promises.

    THE REASON THIS EXISTS RATHER THAN JUST `worst_shortfall`
    ----------------------------------------------------
    The deepest rung essentially always breaches. At a 10% budget, holding the
    promise through a 35% shock means capping equity exposure at 28.6% of
    capital — so a normally invested portfolio is permanently in deficit
    against that row, and an agent that acted on the worst rung would report an
    unclosable gap every single cycle. A warning that is always on is not a
    warning.

    Worse, closing a 35% tail with puts is the expensive problem this project
    started from. Deep protection is bought far out of the money, decays to
    nothing in most years, and costs more than the loss it insures against
    across any ordinary decade.

    So the mandate names one shock it promises against, and that is what the
    agent closes. The deeper rungs stay on the ladder and stay in the journal,
    because "a 2008 would cost you this much and we are deliberately not
    hedging all of it" is a disclosure the client is owed. Reporting it is
    honest; promising it would not be affordable.
    """
    for rung in rungs:
        if abs(rung.shock - shock) < 1e-9:
            return rung
    return None


def bends(options: list[OptionLeg], low: float, high: float) -> list[float]:
    """The shocks strictly inside `(low, high)` where the payoff changes slope.

    An option's expiry payoff is flat on one side of its strike and linear on
    the other, so it bends in exactly one place: the shock that puts the
    underlying at the strike. Shares never bend. A sum of such pieces is
    piecewise linear, and these are all of its breakpoints.

    WHY THIS MEANS NO STEP SIZE IS NEEDED
    --------------------------------------
    A piecewise-linear function on a closed interval attains its maximum at a
    breakpoint or at an endpoint -- between two breakpoints it is a straight
    line, and a straight line is largest at one of its ends. So evaluating the
    ladder at the endpoints plus these points is not a fine sampling of the
    interval, it is the whole interval, exactly. A 0.5% grid would be both
    slower and wrong, since the worst point can fall between grid lines and a
    finer grid only moves the argument about where to stop.

    Endpoints are excluded, and with a tolerance rather than a bare inequality:
    612/765 - 1 is -0.19999999999999996 in binary floating point, which is
    strictly greater than -0.20 and would return a bend a hundredth of a
    femto-percent from the endpoint the caller already evaluates. The maximum
    would be unaffected -- but it would be *reported* at a shock of
    -19.999999999999996%, and a journal line like that reads as a defect
    whether or not it is one.
    """
    out = set()
    for leg in options:
        if leg.spot <= 0:
            continue
        # spot * (1 + shock) == strike
        kink = float(leg.strike) / leg.spot - 1.0
        if low + _EPS < kink < high - _EPS:
            out.add(kink)
    return sorted(out)


def gap_within(
    holdings: list[Holding],
    options: list[OptionLeg],
    budget: float,
    promise: float,
) -> Rung:
    """The worst rung anywhere between no shock and the promised one.

    This is what the agent sizes against. `gap_at` answers "does the book keep
    the promise at exactly this price", which is the question a hedge can be
    tuned to pass while breaking the promise beside it; this answers "does the
    book keep the promise at all", which is what was actually said to the
    client.

    Returns a rung whether or not anything breaches -- `breached` says which.
    Reporting the tightest point of a book that holds is useful on its own: it
    is how much room is left, and it is what `release` needs in order to know
    that handing a hedge back would not immediately reopen the gap.
    """
    low, high = min(promise, 0.0), max(promise, 0.0)
    shocks = tuple(sorted({low, high, *bends(options, low, high)}))
    return max(ladder(holdings, options, budget, shocks), key=lambda r: r.shortfall)


def worst_loss(
    holdings: list[Holding],
    options: list[OptionLeg],
    cost_of_new: float = 0.0,
) -> float:
    """The most the client can lose, anywhere on the way down. No shock chosen.

    `ladder` and `gap_within` answer "how bad is it at this depth", which needs
    somebody to have named a depth. That number was the weakest thing in this
    system: nobody -- not the client, not the agent -- can say whether the next
    fall is 20% or 35%, and a promise that only holds down to a line somebody
    guessed is not much of a promise.

    This asks the question that needs no guess. Shares alone have no answer:
    the loss grows without limit as the price approaches zero. Match puts to
    the shares and the answer becomes finite, because below the strike every
    dollar the shares lose is a dollar the puts gain and the line stops
    falling. The worst case is then the drop down to the strike plus what the
    protection cost, and that is a number the client's budget can be compared
    against directly.

    WHY `cost_of_new` IS A SEPARATE ARGUMENT
    -----------------------------------------
    `ladder` measures the change a shock *causes*, treating premium already
    paid as sunk. That is right for a position the account is carrying -- the
    money left weeks ago and today's equity already reflects it, so charging it
    again would count it twice.

    It is wrong for a position being considered. That premium has not left yet,
    and it will come out of the same account the promise is written against. A
    hedge sized as though its own cost were free comes up short by exactly the
    premium: 100,000 of budget buys protection that lets the client lose
    115,000, and every check reports success. So the cost of anything not yet
    bought is passed in and added on top.
    """
    shocks = tuple(sorted({-1.0, 0.0, *bends(options, -1.0, 0.0)}))
    rungs = ladder(holdings, options, 0.0, shocks)
    return max(-rung.portfolio_loss for rung in rungs) + cost_of_new


def unhedged_limit(budget: float, shock: float) -> float:
    """The largest equity exposure that honours the budget with no protection.

    The mandate sizes the portfolio, not taste. At a 10% budget on 1,000,000
    and a 20% shock this returns 500,000, which is why a 600,000 book has a gap
    to close and a 500,000 one does not.
    """
    if shock >= 0:
        return float("inf")
    return budget / abs(shock)
