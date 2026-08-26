"""Three remedies, priced in three currencies, none of them ranked.

The load-bearing tests are the ones that keep the comparison honest: every
remedy's `gap_after` is the real ladder rather than an estimate, the collar
cannot sell a call it has no shares for, and selling stock is reported as
costing upside rather than as costing nothing.
"""

from decimal import Decimal

import pytest

from flywheel.risk.remedy import collar, protective_put, reduce_exposure
from flywheel.risk.stress import Holding, gap_at, ladder

SPOT = 500.0
SHOCK = -0.20
BUDGET = 100_000.0

# 1,200 shares at 500 is 600,000 of exposure. At -20% that loses 120,000
# against a 100,000 budget: a 20,000 gap, the same shape as the real book.
BOOK = [Holding("SPY", 1200, SPOT), Holding("CASH", 400_000, 1.0, shocked=False)]


def put_row(strike: float, ask: float) -> dict:
    return {"strike": strike, "ask": ask, "bid": ask - 0.10, "right": "P"}


def call_row(strike: float, bid: float) -> dict:
    return {"strike": strike, "bid": bid, "ask": bid + 0.10, "right": "C"}


PUTS = [put_row(460, 6.00), put_row(440, 4.00), put_row(420, 2.50)]
CALLS = [call_row(520, 9.00), call_row(540, 5.00), call_row(560, 2.00)]


def test_the_book_starts_with_the_gap_the_remedies_are_for():
    rung = gap_at(ladder(BOOK, [], BUDGET), SHOCK)
    assert rung.gap == pytest.approx(20_000)


# --- protective put ---------------------------------------------------------


def test_a_protective_put_closes_the_gap_and_says_what_it_cost():
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy is not None
    assert remedy.closes_the_gap
    assert remedy.premium_cost > 0
    # It costs premium and nothing else. That is the whole appeal.
    assert remedy.forgone_upside == 0.0


def test_the_cheapest_total_wins_and_it_is_neither_end_of_the_chain():
    """The optimum is in the middle, and both intuitions about it are wrong.

    At a 20% shock the stock lands at 400, and 20,000 of gap has to be covered:

        460 pays 60 a share -> 4 contracts at 6.00 = 2,400
        440 pays 40 a share -> 5 contracts at 4.00 = 2,000
        420 pays 20 a share -> 10 contracts at 2.50 = 2,500

    A rule reaching for the cheapest sticker price picks 420 and overpays by a
    quarter. A rule reaching for the strongest protection picks 460 and
    overpays by a fifth. Only working out the total finds 440 — which is why
    this searches rather than applies a heuristic about where good strikes sit.

    The first version of this test asserted 460 and 2,400, in a comment written
    with confidence. The code disagreed and the code was right.
    """
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy.legs[0].strike == Decimal("440")
    assert remedy.legs[0].contracts == 5
    assert remedy.premium_cost == pytest.approx(2_000)


def test_the_gap_after_is_the_real_ladder_not_an_estimate():
    """Recomputed with the leg in the book, using the mandate's own arithmetic."""
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    independently = gap_at(ladder(BOOK, remedy.legs, BUDGET), SHOCK)
    assert remedy.gap_after == pytest.approx(independently.gap)


def test_a_book_inside_its_budget_is_offered_nothing():
    """No gap, no remedy. An agent that always had something to sell would be
    an agent with a reason to find a gap."""
    inside = [Holding("SPY", 1000, SPOT)]
    assert protective_put(inside, [], BUDGET, SHOCK, "SPY", SPOT, PUTS) is None
    assert reduce_exposure(inside, [], BUDGET, SHOCK, "SPY") is None


def test_puts_with_no_ask_are_skipped_rather_than_priced_at_zero():
    """A missing quote is not free protection."""
    assert (
        protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, [put_row(460, 0.0)])
        is None
    )


# --- collar -----------------------------------------------------------------


def test_a_collar_costs_less_cash_and_gives_up_the_ceiling():
    put_only = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, CALLS)

    assert ringed is not None
    assert ringed.premium_cost < put_only.premium_cost
    # And the saving is not free. It is quoted at a stated up-move, because
    # "gains above 520" is not a number anyone can weigh.
    assert ringed.forgone_upside > 0
    assert ringed.upside_measured_at == 0.10


def test_the_collar_never_sells_more_calls_than_it_has_shares_for():
    """`forbid_naked` has no exception here.

    The optimizer once proposed four contracts against a hundred shares and the
    gate refused it at every expiry for two and a half years. Two hundred
    shares cover exactly two calls, and this asks for no more.
    """
    thin = [Holding("SPY", 200, SPOT), Holding("CASH", 900_000, 1.0, shocked=False)]
    ringed = collar(thin, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, CALLS)
    if ringed is not None:
        short_calls = [leg for leg in ringed.legs if leg.right == "C"]
        assert abs(short_calls[0].contracts) <= 200 // 100


def test_a_collar_with_no_shares_at_all_is_not_offered():
    """Nothing to cover the call, so there is no collar — not a naked one."""
    borrowed = [
        Holding("QQQ", 2000, 400.0),
        Holding("CASH", 200_000, 1.0, shocked=False),
    ]
    assert collar(borrowed, [], BUDGET, SHOCK, "SPY", 400.0, PUTS, CALLS) is None


def test_the_highest_funding_call_is_chosen_so_less_upside_is_given_up():
    """Among calls that pay for the put, the furthest one is strictly better.

    Every one of them funds the protection; the only thing separating them is
    how much ceiling the client keeps.
    """
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, CALLS)
    call_leg = next(leg for leg in ringed.legs if leg.right == "C")
    # The put leg is 5 contracts at 4.00, so 2,000, which is 4.00 a share to
    # fund. The 520 call bids 9.00 and the 540 bids 5.00: both cover it, and
    # 540 leaves the client 20 more points of ceiling for no worse funding.
    assert call_leg.strike == Decimal("540")
    assert abs(call_leg.contracts) == 5


def test_an_in_the_money_call_is_never_sold_to_fund_protection():
    """That is not financing a hedge, it is agreeing to sell at a loss."""
    cheap = [call_row(480, 30.0), call_row(540, 5.00)]
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, cheap)
    call_leg = next(leg for leg in ringed.legs if leg.right == "C")
    assert float(call_leg.strike) > SPOT


# --- reduce exposure --------------------------------------------------------


def test_selling_shares_closes_the_gap_with_no_premium():
    remedy = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert remedy is not None
    assert remedy.premium_cost == 0.0
    assert remedy.closes_the_gap
    # 20,000 of gap at a 20% shock on 500 shares: 100 a share of loss removed,
    # so 200 shares.
    assert remedy.shares_sold["SPY"] == 200


def test_selling_shares_is_not_reported_as_costing_nothing():
    """The correction to a claim made earlier in this project and withdrawn.

    "Selling stock costs nothing" is false: it costs participation in every
    future gain on what was sold. Zero premium is not zero cost, and a report
    that showed only the premium column would make this look like the obvious
    answer every time.
    """
    remedy = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert remedy.forgone_upside > 0
    assert remedy.forgone_upside == pytest.approx(200 * SPOT * 0.10)


def test_the_proceeds_stay_in_the_book_as_cash_that_does_not_move():
    """Selling does not delete the money, and the ladder has to see that.

    If the proceeds vanished, the remedy would appear to close far more of the
    gap than it does, because the ladder would be measuring a smaller
    portfolio rather than a less exposed one.
    """
    remedy = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert remedy.gap_after <= 500.0
    assert remedy.gap_before == pytest.approx(20_000)


# --- the comparison itself --------------------------------------------------


def test_the_three_are_priced_in_three_currencies_and_never_summed():
    """The reason there is no single score.

    Certain premium, contingent ceiling, and permanent participation are not
    commensurable without a view on where the market goes. Each remedy reports
    its own, and the line it prints keeps them in separate columns.
    """
    put_only = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, CALLS)
    sold = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")

    assert (put_only.premium_cost, put_only.forgone_upside) == (2_000.0, 0.0)
    assert ringed.premium_cost < put_only.premium_cost
    assert ringed.forgone_upside > 0
    assert sold.premium_cost == 0.0
    assert sold.forgone_upside > 0

    # All three close the same gap. Nothing about that makes them equivalent.
    for remedy in (put_only, ringed, sold):
        assert remedy.closes_the_gap
        assert "premium" in remedy.line() or "credit" in remedy.line()


def test_a_partial_remedy_is_not_reported_as_closing_the_gap():
    """Contracts are lumpy, but "nearly" is not "yes"."""
    far = [put_row(410, 1.00)]
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, far)
    if remedy is not None:
        assert remedy.closes_the_gap


def test_a_collar_that_collects_more_than_it_spends_reports_a_credit():
    """Not clamped to zero, because zero would hide that it pays.

    On the real book the call outsold the put by 600. A field that could not go
    below zero would have shown "0 premium" for a position that puts money in
    the client's account, and the collar would have looked merely free instead
    of better than free -- while still costing 15,000 of ceiling, which is the
    part that actually needs weighing.
    """
    rich = [call_row(520, 20.0), call_row(540, 12.0)]
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, rich)
    assert ringed.premium_cost < 0
    assert "credit" in ringed.line()
    assert ringed.forgone_upside > 0


def test_a_ceiling_above_the_measured_move_reports_no_cost_at_that_move():
    """True, and the reason `upside_measured_at` is on the record beside it.

    A 560 call with the stock at 500 gives up nothing by +10%, because +10% is
    550. `forgone_upside` is a reading at one point, not a summary of the whole
    payoff, and a zero here means "not at this move" rather than "not ever" --
    past +12% that call starts costing real money.
    """
    high = [call_row(560, 12.0)]
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, high)
    assert ringed.forgone_upside == 0.0
    assert ringed.upside_measured_at == 0.10
    assert float(next(x for x in ringed.legs if x.right == "C").strike) > SPOT * 1.10

    # Move the reading out to +20% and the ceiling shows up.
    further = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, high, up_move=0.20)
    assert further.forgone_upside > 0
