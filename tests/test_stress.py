"""The stress ladder: what the book loses, and whether that breaks the promise.

The arithmetic here is checkable by hand on purpose, so every test states the
number it expects rather than recomputing it from the same code under test.
"""

from decimal import Decimal

import pytest

from flywheel.risk.stress import (
    DEFAULT_SHOCKS,
    Holding,
    OptionLeg,
    describe,
    ladder,
    unhedged_limit,
    worst_gap,
)

# 600,000 of equity: 392 SPY at 765, 211 QQQ at 711, 501 IWM at 299.
BOOK = [
    Holding("SPY", 392, 765.0),
    Holding("QQQ", 211, 711.0),
    Holding("IWM", 501, 299.0),
    Holding("BIL", 2728, 91.6, shocked=False),
    Holding("CASH", 150214, 1.0, shocked=False),
]
BUDGET = 100_000.0


def equity_value() -> float:
    return sum(h.value for h in BOOK if h.shocked)


# --- the shape of the promise ----------------------------------------------


def test_the_mandate_sizes_the_portfolio():
    """The limit is derived, not chosen.

    A 100,000 budget against a 20% shock permits exactly 500,000 of unhedged
    equity. That is why a 600,000 book has work to do.
    """
    assert unhedged_limit(100_000, -0.20) == 500_000
    assert unhedged_limit(100_000, -0.10) == 1_000_000


def test_small_shocks_stay_inside_the_promise():
    rungs = ladder(BOOK, [], BUDGET)
    for rung in rungs:
        if rung.shock >= -0.10:
            assert not rung.breached, f"{rung.shock} should be inside the budget"


def test_a_twenty_percent_shock_breaches_and_the_gap_is_the_overshoot():
    rungs = ladder(BOOK, [], BUDGET)
    twenty = next(r for r in rungs if r.shock == -0.20)
    expected_loss = -equity_value() * 0.20
    assert twenty.portfolio_loss == pytest.approx(expected_loss)
    assert twenty.gap == pytest.approx(-expected_loss - BUDGET)
    assert twenty.breached


def test_cash_and_bills_do_not_move_and_are_not_called_a_hedge():
    """Short-duration Treasuries barely respond to an equity shock.

    Treating them as flat is the honest modelling choice. In 2022 long bonds
    fell alongside equities, and a portfolio that books a bond as insurance
    finds out at the worst moment.
    """
    only_ballast = [Holding("BIL", 2728, 91.6, shocked=False)]
    rungs = ladder(only_ballast, [], BUDGET)
    assert all(r.portfolio_loss == pytest.approx(0.0) for r in rungs)


# --- what options do to the ladder -----------------------------------------


def long_put(strike, premium, contracts=1, spot=765.0) -> OptionLeg:
    return OptionLeg(
        "SPY", "P", Decimal(str(strike)), contracts, Decimal(str(premium)), spot
    )


def short_call(strike, premium, contracts=-1, spot=765.0) -> OptionLeg:
    return OptionLeg(
        "SPY", "C", Decimal(str(strike)), contracts, Decimal(str(premium)), spot
    )


def test_a_long_put_pays_in_the_shock_and_shrinks_the_gap():
    bare = ladder(BOOK, [], BUDGET)
    hedged = ladder(BOOK, [long_put(700, 8.0, contracts=3)], BUDGET)
    bare_20 = next(r for r in bare if r.shock == -0.20)
    hedged_20 = next(r for r in hedged if r.shock == -0.20)
    assert hedged_20.gap < bare_20.gap
    assert hedged_20.protected_by_options > 0


def test_the_put_payout_is_exact_and_checkable_by_hand():
    """SPY 765 falls 20% to 612. A 700 put is 88 in the money."""
    leg = long_put(700, 8.0, contracts=1)
    # At -20%: intrinsic 88, premium 8 already paid -> +80 a share, 100 shares.
    assert leg.pnl_at(-0.20) == pytest.approx(8000.0)
    # Unshocked the put is out of the money and the premium is simply spent.
    assert leg.pnl_at(0.0) == pytest.approx(-800.0)


def test_a_short_call_helps_a_little_on_the_way_down_and_caps_the_way_up():
    """The premium cushions the first part of a fall. It is not protection,
    but it is not nothing either, and the ladder must count it."""
    leg = short_call(800, 6.0, contracts=-1)
    assert leg.pnl_at(-0.20) == pytest.approx(600.0)  # expires worthless, keep it
    assert leg.pnl_at(0.30) < 0  # called away above the strike


def test_a_short_put_makes_the_gap_worse_which_is_the_whole_tension():
    """Selling puts is income, and income here costs downside budget.

    The two halves of the wheel sit on opposite sides of the promise, and this
    is the test that says so.
    """
    bare = ladder(BOOK, [], BUDGET)
    with_csp = ladder(
        BOOK,
        [OptionLeg("SPY", "P", Decimal("700"), -3, Decimal("8.0"), 765.0)],
        BUDGET,
    )
    assert (
        next(r for r in with_csp if r.shock == -0.20).gap
        > next(r for r in bare if r.shock == -0.20).gap
    )


# --- the ladder as a whole --------------------------------------------------


def test_the_worst_breach_is_the_one_reported():
    rungs = ladder(BOOK, [], BUDGET)
    worst = worst_gap(rungs)
    assert worst is not None
    assert worst.shock == -0.35  # the deepest shock breaches by the most


def test_no_breach_returns_nothing_rather_than_a_zero_rung():
    """A portfolio inside its mandate has no gap, and saying so with None
    rather than a zero keeps 'nothing to do' distinct from 'a gap of zero'."""
    tiny = [Holding("SPY", 10, 765.0)]
    assert worst_gap(ladder(tiny, [], BUDGET)) is None


def test_the_ladder_is_fixed_and_does_not_follow_the_market():
    """A ladder that moved with prices would let a bad day redefine safe."""
    assert DEFAULT_SHOCKS == (-0.05, -0.10, -0.20, -0.35)


def test_the_table_names_the_breaches():
    text = describe(ladder(BOOK, [], BUDGET))
    assert "BREACH" in text
    assert "-20%" in text or "-20" in text
