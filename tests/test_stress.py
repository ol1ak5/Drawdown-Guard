"""The stress ladder: what the book loses, and whether that breaks the promise.

The arithmetic here is checkable by hand on purpose, so every test states the
number it expects rather than recomputing it from the same code under test.
"""

from decimal import Decimal

import pytest

from drawdownguard.risk.stress import (
    DEFAULT_SHOCKS,
    Holding,
    OptionLeg,
    bends,
    describe,
    gap_within,
    ladder,
    unhedged_limit,
    worst_gap,
    worst_loss,
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


# --- the promise is an interval ---------------------------------------------
#
# BOOK holds 599,700 of equity against a 100,000 budget, so an unprotected 20%
# shock loses 119,940 and leaves a gap of 19,940. Every number below is that
# arithmetic and nothing else.


def test_only_strikes_bend_the_payoff():
    """Shares are straight lines; options bend once each, at their strike.

    765 * (1 - 0.20) = 612, so a 612 strike bends exactly at the promise and is
    excluded as an endpoint rather than an interior point.
    """
    assert bends([], -0.20, 0.0) == []
    assert bends([long_put(688.5, 5.0)], -0.20, 0.0) == pytest.approx([-0.10])
    # 612 is the endpoint itself, 550 is past it: neither is interior.
    assert bends([long_put(612.0, 5.0), long_put(550.0, 5.0)], -0.20, 0.0) == []


def test_shares_alone_cannot_hide_a_breach_between_the_rungs():
    """With no options the deepest point is the worst one, so checking the
    endpoint really was checking everything. This is why the defect below went
    unnoticed until the agent started buying options."""
    worst = gap_within(BOOK, [], BUDGET, -0.20)
    assert worst.shock == pytest.approx(-0.20)
    assert worst.gap == pytest.approx(19_940)


def test_a_hedge_can_pass_at_the_promise_and_break_just_above_it():
    """The defect this whole section exists for.

    Ten 632 puts pay 20 a share at a 20% shock -- 20,000 against a 19,940 gap,
    so the mandate holds at exactly the promised price and a point check
    reports success.

    Two percent higher the same hedge is nearly worthless. At -18% the book has
    lost 107,946 and the puts return only 4,700, so the client is 3,246 past a
    budget they were told was intact. Nothing about the hedge is dishonest: it
    is simply the cheapest structure that passes the test that was being run,
    which is what sizing against a single point selects for.
    """
    hedge = [long_put(632.0, 8.0, contracts=10)]

    at_the_promise = next(r for r in ladder(BOOK, hedge, BUDGET, (-0.20,)))
    assert at_the_promise.gap == pytest.approx(0.0)
    assert not at_the_promise.breached

    beside_it = next(r for r in ladder(BOOK, hedge, BUDGET, (-0.18,)))
    assert beside_it.gap == pytest.approx(3_246)
    assert beside_it.breached


def test_the_interval_check_finds_the_breach_and_lands_on_the_bend():
    """Same book, same hedge, the question asked properly.

    The worst point is the strike itself: below it the puts start paying, above
    it there is less to lose. 632/765 - 1 = -17.386%, where the book is down
    104,261.57 with no protection yet, for a gap of 4,261.57 -- larger than the
    3,246 that a -18% probe happened to catch, because the true worst point
    does not fall on any round number a grid would have chosen.
    """
    hedge = [long_put(632.0, 8.0, contracts=10)]
    worst = gap_within(BOOK, hedge, BUDGET, -0.20)

    assert worst.shock == pytest.approx(632.0 / 765.0 - 1.0)
    assert worst.gap == pytest.approx(4_261.57, abs=0.01)
    assert worst.breached


# --- the worst case, with nobody naming a depth -----------------------------
#
# A round book so the arithmetic is checkable in the head: 8,000 shares at 100
# is 800,000 of equity, held inside a 1,000,000 account whose budget is
# 100,000. Puts struck at 90.04 cost 2.54 and there are 80 of them, one per
# hundred shares.

ROUND = [
    Holding("EQ", 8000, 100.0),
    Holding("RESERVE", 200_000, 1.0, shocked=False),
]


def matched_put(strike=90.04, premium=2.54, contracts=80) -> OptionLeg:
    return OptionLeg(
        "EQ", "P", Decimal(str(strike)), contracts, Decimal(str(premium)), 100.0
    )


def test_shares_alone_have_no_worst_case_short_of_everything():
    """Nothing bounds a share but zero, which is why the promise needs help."""
    assert worst_loss(ROUND, []) == pytest.approx(800_000)


def test_matched_puts_stop_the_loss_at_the_strike():
    """Below 90.04 each dollar the shares lose is a dollar the puts gain, so
    the answer is the fall down to the strike and nothing beyond it."""
    assert worst_loss(ROUND, [matched_put()]) == pytest.approx(79_680)


def test_a_hedge_being_bought_is_charged_for_itself():
    """The defect this argument exists for.

    79,680 of fall plus 20,320 of premium is exactly the 100,000 budget. Size
    the same hedge without charging its own cost and it reports 79,680 -- room
    to spare -- while the client is in fact spending the whole budget. The gap
    between the two numbers is the premium, every time.
    """
    cost = 2.54 * 8000
    assert worst_loss(ROUND, [matched_put()], cost) == pytest.approx(100_000)


def test_too_few_puts_leave_the_loss_unbounded():
    """Half the shares covered is not half the promise kept. Below the strike
    the uncovered shares keep falling, and the worst case runs away again."""
    half = worst_loss(ROUND, [matched_put(contracts=40)])
    assert half > 400_000


def test_a_hedge_can_pass_the_chosen_depth_and_still_be_the_wrong_hedge():
    """Why naming a depth was the weak point.

    An unprotected 20% shock costs 160,000 against a 100,000 budget, so 60,000
    has to come from somewhere. Sixty puts struck 90 are 10 in the money at
    that price and return exactly 60,000: the mandate holds at -20%, and any
    check that asks only about -20% reports success.

    They cover 6,000 of the client's 8,000 shares. Below the strike the other
    2,000 keep falling with nothing behind them, and the worst case is 275,000
    -- nearly three times the budget the client was promised. The hedge is not
    too small for the shock that was tested; it is too small for the client.
    """
    thin = OptionLeg("EQ", "P", Decimal("90"), 60, Decimal("2.5"), 100.0)

    at_twenty = next(r for r in ladder(ROUND, [thin], 100_000.0, (-0.20,)))
    assert not at_twenty.breached
    assert at_twenty.gap == pytest.approx(0.0)

    assert worst_loss(ROUND, [thin], 2.5 * 6000) == pytest.approx(275_000)


def test_a_book_that_holds_still_reports_where_it_is_tightest():
    """Returned as a rung rather than None, unlike `worst_gap`.

    'The promise holds, and here is the least room it has' is what tells the
    agent a hedge can be released without the gap reopening the moment it goes.
    """
    small = [Holding("SPY", 100, 765.0)]
    worst = gap_within(small, [], BUDGET, -0.20)
    assert not worst.breached
    assert worst.gap == 0.0
    assert worst.shock == pytest.approx(-0.20)
