"""Spending part of today's income to change the shape of tomorrow's loss.

A cash-secured put pays well and loses without a floor: if the underlying goes
to zero you own it at the strike. Buying a further-out-of-the-money put against
it puts a floor under that, and the floor is paid for out of the premium the
short leg just collected.

So the agent has a decision nobody has to predict the market to make:

    premium collected            300
    protection costs              60   -> 20% of the income
    worst case without it     -50,000
    worst case with it         -1,940

    Is a fifth of the income worth capping the loss at two thousand?

That question has an answer today, from prices printed today. It needs no view
on where the market is going, which is why it belongs in this project.

WHY THIS DOES NOT TOUCH `forbid_naked`
--------------------------------------
The short leg stays **fully cash-secured**. This is not a margin spread — we do
not use the long put to reduce collateral, and the account still sets aside the
whole strike. The long put is therefore pure additive protection, and every
existing risk rule passes unchanged.

That choice costs capital efficiency and buys the ability to ship. A margin
spread would mean redefining what "covered" means, which is the one rule in
this project with no override anywhere.

WHAT THE PROTECTED POSITION IS WORTH AT EXPIRY
----------------------------------------------
Per share, for a short put at `K_short` and a long put at `K_long < K_short`:

    payoff = premium_net - max(K_short - S, 0) + max(K_long - S, 0)

Below `K_long` the two intrinsic terms move together and the loss stops. The
floor is `K_short - K_long` per share, less the net premium.
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from drawdownguard.domain import SHARES_PER_CONTRACT

# A protective leg costing more than this share of the premium is not bought.
# Above it the position stops being an income trade: most of what was collected
# goes straight back out, and the remainder does not pay for the capital tied
# up. The number is a starting point to be calibrated on the backtest, not a
# law — `scripts/study_protection.py` is what moves it.
MAX_PROTECTION_COST_PCT = 35.0

# How far below the short strike to look for the floor, as a share of spot.
# Nearer than this and the protection costs most of the premium; further and it
# stops being protection and becomes a lottery ticket.
FLOOR_SEARCH = (0.03, 0.15)


@dataclass(frozen=True)
class ProtectedPut:
    """A cash-secured put with a floor bought out of its own premium."""

    short_strike: Decimal
    long_strike: Decimal
    short_premium: Decimal  # per share, received
    long_cost: Decimal  # per share, paid
    contracts: int
    expiry: object
    symbol: str

    @property
    def net_premium(self) -> Decimal:
        return self.short_premium - self.long_cost

    @property
    def protection_cost_pct(self) -> float:
        """What the floor cost, as a share of the income it came out of."""
        if self.short_premium <= 0:
            return 100.0
        return float(self.long_cost / self.short_premium * 100)

    @property
    def worst_case(self) -> Decimal:
        """The most this position can lose, in dollars. Finite by construction.

        An unprotected cash-secured put has no equivalent: its floor is the
        strike going to zero.
        """
        width = self.short_strike - self.long_strike
        return (width - self.net_premium) * self.contracts * SHARES_PER_CONTRACT

    @property
    def income(self) -> Decimal:
        return self.net_premium * self.contracts * SHARES_PER_CONTRACT

    def describe(self) -> str:
        return (
            f"{self.symbol} {self.short_strike}/{self.long_strike} put, "
            f"{self.contracts}x — income {self.income:,.0f}, "
            f"worst case {-self.worst_case:,.0f}, "
            f"floor cost {self.protection_cost_pct:.0f}% of premium"
        )


def payoff_at(prices: np.ndarray, position: ProtectedPut) -> np.ndarray:
    """Dollar payoff of the protected position across terminal prices."""
    short_k = float(position.short_strike)
    long_k = float(position.long_strike)
    net = float(position.net_premium)
    per_share = (
        net - np.maximum(short_k - prices, 0.0) + np.maximum(long_k - prices, 0.0)
    )
    return per_share * position.contracts * SHARES_PER_CONTRACT


def unprotected_payoff(prices: np.ndarray, position: ProtectedPut) -> np.ndarray:
    """The same short leg with no floor, for comparison.

    Reported next to the protected version rather than hidden: the whole
    decision is a trade between income now and a bounded loss later, and only
    showing the version we chose would make the trade look free.
    """
    short_k = float(position.short_strike)
    premium = float(position.short_premium)
    per_share = premium - np.maximum(short_k - prices, 0.0)
    return per_share * position.contracts * SHARES_PER_CONTRACT


def choose_floor(
    short_strike: Decimal,
    short_premium: Decimal,
    candidates: list[dict],
    spot: float,
    max_cost_pct: float = MAX_PROTECTION_COST_PCT,
) -> dict | None:
    """The best floor to buy under a short put, or None to stay unprotected.

    "Best" is the **highest** strike the budget affords — the nearest floor, not
    the furthest.

    The first version of this function took the lowest strike, reasoning that
    the tail is what hurts so the floor should sit far out. That reads well and
    is wrong, and a test caught it. The floor's job is to cap the loss, and the
    cap is the distance between the two strikes: a floor at 580 under a short
    600 caps the loss at 20 a share, one at 540 caps it at 60. The distant
    floor is cheaper because it protects less.

    Returns None when nothing affordable exists, and that is a real answer —
    on a day when protection is expensive, the honest move is to collect the
    income unprotected or not trade at all, not to buy a floor that eats the
    trade.

    `candidates` are chain rows for the same expiry, in the shape the chain
    adapter produces.
    """
    if short_premium <= 0:
        return None
    budget = short_premium * Decimal(str(max_cost_pct / 100))
    near, far = FLOOR_SEARCH
    low = Decimal(str(spot * (1 - far)))
    high = Decimal(str(spot * (1 - near)))

    affordable = [
        row
        for row in candidates
        if row["right"] == "P"
        and low <= row["strike"] <= high
        and row["strike"] < short_strike
        and Decimal(str(row["ask"])) <= budget
        and Decimal(str(row["ask"])) > 0
    ]
    if not affordable:
        return None
    # Highest strike that fits: the nearest floor, so the cap is tightest.
    return max(affordable, key=lambda row: row["strike"])


def worth_protecting(
    short_premium: Decimal, floor_cost: Decimal, max_cost_pct: float
) -> tuple[bool, str]:
    """Whether the floor is worth its price, and the sentence explaining it.

    Both outcomes are decisions worth journalling. "Protection was available
    and we declined it because it cost 82% of the income" is as much a choice
    as buying it, and a log that only recorded purchases would read as an agent
    that never considered the alternative.
    """
    if short_premium <= 0:
        return False, "no premium to spend"
    pct = float(floor_cost / short_premium * 100)
    if pct <= max_cost_pct:
        return True, (
            f"floor costs {pct:.0f}% of the premium, inside the "
            f"{max_cost_pct:.0f}% budget — bought"
        )
    return False, (
        f"floor costs {pct:.0f}% of the premium, over the "
        f"{max_cost_pct:.0f}% budget — declined, income taken unprotected"
    )
