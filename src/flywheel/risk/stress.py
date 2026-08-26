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
from decimal import Decimal

from flywheel.domain import SHARES_PER_CONTRACT

# The scenarios the mandate is checked against. Fixed, published, and the same
# every day: a ladder that moved with the market would let a bad day redefine
# what "safe" means.
DEFAULT_SHOCKS: tuple[float, ...] = (-0.05, -0.10, -0.20, -0.35)


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
    def gap(self) -> float:
        """How far past the promise this scenario takes the portfolio.

        Zero when the mandate holds. Positive is the dollars of protection the
        agent still has to find.
        """
        return max(-self.portfolio_loss - self.budget, 0.0)

    @property
    def breached(self) -> bool:
        return self.gap > 0


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


def worst_gap(rungs: list[Rung]) -> Rung | None:
    """The rung that breaches the mandate by the most, or None if none do.

    Reported, but not what the agent acts on — see `gap_at`.
    """
    breaches = [r for r in rungs if r.breached]
    return max(breaches, key=lambda r: r.gap) if breaches else None


def gap_at(rungs: list[Rung], shock: float) -> Rung | None:
    """The rung at one specific shock: the one the mandate actually promises.

    THE REASON THIS EXISTS RATHER THAN JUST `worst_gap`
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


def unhedged_limit(budget: float, shock: float) -> float:
    """The largest equity exposure that honours the budget with no protection.

    The mandate sizes the portfolio, not taste. At a 10% budget on 1,000,000
    and a 20% shock this returns 500,000, which is why a 600,000 book has a gap
    to close and a 500,000 one does not.
    """
    if shock >= 0:
        return float("inf")
    return budget / abs(shock)


def describe(rungs: list[Rung]) -> str:
    """The ladder as a table, for the journal and the status page."""
    lines = [
        f"{'shock':>7}{'portfolio':>14}{'from options':>15}{'budget':>12}{'gap':>12}",
        "-" * 60,
    ]
    for rung in rungs:
        flag = "  BREACH" if rung.breached else ""
        lines.append(
            f"{rung.shock * 100:>6.0f}%{rung.portfolio_loss:>14,.0f}"
            f"{rung.protected_by_options:>15,.0f}{-rung.budget:>12,.0f}"
            f"{rung.gap:>12,.0f}{flag}"
        )
    return "\n".join(lines)
