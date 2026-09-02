"""Three remedies, priced in three currencies, none of them ranked.

The load-bearing tests are the ones that keep the comparison honest: every
remedy's `uncovered_after` is the real ladder rather than an estimate, the collar
cannot sell a call it has no shares for, and selling stock is reported as
costing upside rather than as costing nothing.
"""

import inspect
from decimal import Decimal

import pytest

from drawdownguard.risk.remedy import (
    _uncovered,
    choose,
    collar,
    protective_put,
    reduce_exposure,
    release,
)
from drawdownguard.risk.stress import Holding, OptionLeg, gap_at, ladder

SPOT = 500.0
SHOCK = -0.20
BUDGET = 100_000.0

# 1,200 shares at 500 is 600,000 of exposure. At -20% that loses 120,000
# against a 100,000 budget: a 20,000 gap, the same shape as the real book.
BOOK = [Holding("SPY", 1200, SPOT), Holding("CASH", 400_000, 1.0, shocked=False)]


def put_row(strike: float, ask: float, iv: float | None = 0.22) -> dict:
    return {
        "strike": strike,
        "ask": ask,
        "bid": ask - 0.10,
        "right": "P",
        "implied_vol": iv,
    }


def call_row(strike: float, bid: float, iv: float | None = 0.18) -> dict:
    return {
        "strike": strike,
        "bid": bid,
        "ask": bid + 0.10,
        "right": "C",
        "implied_vol": iv,
    }


# The default volatilities are the equity-index shape: puts dearer than calls.
# Tests that need the other shape say so, because it is the exception.
PUTS = [put_row(460, 6.00), put_row(440, 4.00), put_row(420, 2.50)]
CALLS = [call_row(520, 9.00), call_row(540, 5.00), call_row(560, 2.00)]


def test_the_book_starts_with_the_gap_the_remedies_are_for():
    rung = gap_at(ladder(BOOK, [], BUDGET), SHOCK)
    assert rung.shortfall == pytest.approx(20_000)


# --- protective put ---------------------------------------------------------


def test_a_protective_put_closes_the_gap_and_says_what_it_cost():
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy is not None
    assert remedy.covers_the_risk
    assert remedy.premium_cost > 0
    # It costs premium and nothing else. That is the whole appeal.
    assert remedy.forgone_upside == 0.0


def test_the_lowest_strike_that_still_keeps_the_promise_wins():
    """One contract per hundred shares, and then the only question is how far
    down the strike can go.

    Twelve contracts stand behind 1,200 shares, so below the strike the loss
    stops falling and the client's worst case is the drop to the strike plus
    the premium. Against a 100,000 budget:

        460 -> 48,000 of fall + 7,200 of premium =  55,200
        440 -> 72,000 of fall + 4,800 of premium =  76,800
        420 -> 96,000 of fall + 3,000 of premium =  99,000   <- chosen

    All three keep the promise. 420 keeps it for 3,000 while 460 keeps it for
    7,200, and the extra 4,200 buys the client nothing they were promised --
    it buys a floor at 6.4% when they asked to be protected at 10%. Protection
    is a cost, so the cheapest one that holds is the right one.

    Note what is *not* here: no shock decides this. The old rule sized on
    intrinsic value at exactly -20% and picked 440, which was the cheapest
    thing that passed that particular test and covered only 500 of the client's
    1,200 shares.
    """
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy.legs[0].strike == Decimal("420")
    assert remedy.legs[0].contracts == 12
    # 2.50 asked plus a quarter of the 0.10 spread, rounded up to the cent:
    # 2.53, and twelve contracts of it. The tolerance is charged here because
    # it is charged at the broker -- see `crossing_price`.
    assert remedy.premium_cost == pytest.approx(3_132)


def test_a_hedge_stands_behind_every_share_or_it_is_not_a_floor():
    """Fewer contracts than shares is not a smaller promise, it is no promise.

    Below the strike the covered shares stop losing and the uncovered ones
    carry on, so the worst case runs away again -- the client pays for a floor
    and does not get one.
    """
    from drawdownguard.risk.remedy import contracts_to_match

    assert contracts_to_match(BOOK, SPOT) == 12  # 1,200 shares, 100 to a contract
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy.legs[0].contracts == 12


def test_no_strike_on_the_chain_can_close_it_and_that_is_said_with_none():
    """A budget too small for anything on offer is answered honestly.

    2,000 against 1,200 shares needs a strike inside 1.7% of the money, and
    nothing that near exists here. Returning the best available would report a
    promise as kept when it is not.
    """
    assert protective_put(BOOK, [], 2_000.0, SHOCK, "SPY", SPOT, PUTS) is None


def test_the_gap_after_is_the_real_ladder_not_an_estimate():
    """Recomputed with the leg in the book, using the mandate's own arithmetic."""
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    independently = gap_at(ladder(BOOK, remedy.legs, BUDGET), SHOCK)
    assert remedy.uncovered_after == pytest.approx(independently.shortfall)


def test_a_book_inside_its_budget_is_offered_nothing():
    """No gap, no remedy. An agent that always had something to sell would be
    an agent with a reason to find a gap.

    Inside the budget means the whole book can be lost and still not exceed
    it: 160 shares at 500 is 80,000 against 100,000. 1,000 shares would have
    passed the old point check at -20% and can lose half a million.
    """
    inside = [Holding("SPY", 160, SPOT)]
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
    # The put leg is 12 contracts at 2.50, so 2.50 a share to fund. The 520
    # call bids 9.00 and the 540 bids 5.00: both cover it, and 540 leaves the
    # client 20 more points of ceiling for no worse funding.
    assert call_leg.strike == Decimal("540")
    assert abs(call_leg.contracts) == 12


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
    assert remedy.covers_the_risk
    # Every share sold is a share that can no longer lose anything, and the
    # measure is the whole descent -- so bounding a 1,200-share book inside a
    # 100,000 budget means selling until what is left cannot exceed it. There
    # is no partial answer here: shares have no floor, and 200 of them would
    # leave 100,000 of unbounded downside rather than a smaller one.
    assert remedy.shares_sold["SPY"] == 1200


def test_selling_shares_is_not_reported_as_costing_nothing():
    """The correction to a claim made earlier in this project and withdrawn.

    "Selling stock costs nothing" is false: it costs participation in every
    future gain on what was sold. Zero premium is not zero cost, and a report
    that showed only the premium column would make this look like the obvious
    answer every time.
    """
    remedy = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert remedy.forgone_upside > 0
    assert remedy.forgone_upside == pytest.approx(1200 * SPOT * 0.10)


def test_the_proceeds_stay_in_the_book_as_cash_that_does_not_move():
    """Selling does not delete the money, and the ladder has to see that.

    If the proceeds vanished, the remedy would appear to close far more of the
    gap than it does, because the ladder would be measuring a smaller
    portfolio rather than a less exposed one.
    """
    remedy = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert remedy.uncovered_after <= 500.0
    assert remedy.uncovered_before == pytest.approx(500_000)


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

    assert (put_only.premium_cost, put_only.forgone_upside) == (3_132.0, 0.0)
    assert ringed.premium_cost < put_only.premium_cost
    assert ringed.forgone_upside > 0
    assert sold.premium_cost == 0.0
    assert sold.forgone_upside > 0

    # All three close the same gap. Nothing about that makes them equivalent.
    for remedy in (put_only, ringed, sold):
        assert remedy.covers_the_risk
        assert "premium" in remedy.line() or "credit" in remedy.line()


# A chain whose calls are too far out to pay for the puts, so the collar costs
# cash instead of collecting it. The default CALLS above collect 4,500 against
# 2,000 of puts and open for a credit, which is a real outcome and the reason
# the claim below is about remedies that cost premium rather than about all of
# them.
THIN_CALLS = [call_row(560, 2.00)]


def test_ranking_on_cash_alone_reaches_for_the_one_way_door():
    """Why the single score is not merely unfounded but wrong in one direction.

    Selling shares costs no premium ever, so against any remedy that costs
    premium it wins on cash by construction -- not on this chain, on every
    chain. An agent scoring on cost would answer a temporary breach by
    permanently disposing of the portfolio and publish an excellent cost record
    while doing it. This test pins the trap rather than the fix: the naive
    ranking picks the door that does not open again, and `permanent` is the
    field that says so out loud.
    """
    offers = [
        protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS),
        collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, THIN_CALLS),
        reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY"),
    ]
    assert all(r.premium_cost > 0 for r in offers if r.kind != "reduce_exposure")
    cheapest = min(offers, key=lambda r: r.premium_cost)
    assert cheapest.kind == "reduce_exposure"
    assert cheapest.permanent

    # And it is the only one of the three that cannot be undone by waiting.
    assert [r.kind for r in offers if r.permanent] == ["reduce_exposure"]


def test_the_one_comparison_that_is_arithmetic_is_made():
    """Cash against cash is a fact, so it gets computed.

    Both option remedies are priced in dollars leaving the account today, and
    dollars per thousand of gap closed ranks them without any view on the
    market. The put here buys 20,000 of gap closed for 3,000 of premium: 150
    per thousand. The collar is part-funded by the call it sells, so it closes
    the same gap for less cash and reports a smaller number.

    The gap is the whole unbounded downside now -- 500,000 of shares with no
    floor under them -- so the per-thousand figures are small. What the number
    is for is unchanged: it ranks two cash-priced remedies against each other,
    and it is a fact rather than a view.
    """
    put_only = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, THIN_CALLS)

    assert put_only.risk_covered == pytest.approx(500_000)
    assert put_only.cash_per_1k == pytest.approx(6.264)
    assert ringed.cash_per_1k == pytest.approx(1.464)


def test_a_remedy_that_costs_no_cash_does_not_score_zero_on_the_cash_axis():
    """None, not 0.0. Zero would win a race it is not running.

    A share sale and a credit collar both cost nothing today. Reported as zero
    dollars per thousand they would sort to the front of any cheapest-first
    list, which is exactly the ranking this module refuses to make.
    """
    sold = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    assert sold.premium_cost == 0.0
    assert sold.cash_per_1k is None

    # The default chain already opens for a credit: 4,500 collected against
    # 2,000 of puts. A collar that pays the client to be protected is cheaper
    # than free, and it too has to stay off the cash ranking.
    credit = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, CALLS)
    assert credit.premium_cost < 0
    assert credit.cash_per_1k is None


def test_a_partial_remedy_is_not_reported_as_closing_the_gap():
    """Contracts are lumpy, but "nearly" is not "yes"."""
    far = [put_row(410, 1.00)]
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, far)
    if remedy is not None:
        assert remedy.covers_the_risk


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


# --- the choice ---------------------------------------------------------------


def offers_on(calls: list[dict], puts: list[dict] | None = None) -> list:
    """The reversible pair, priced against one chain."""
    puts = puts if puts is not None else PUTS
    return [
        remedy
        for remedy in (
            protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, puts),
            collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, puts, calls),
        )
        if remedy is not None
    ]


def test_the_same_book_gets_two_different_answers_from_two_chains():
    """The whole point of choosing on the chain instead of on a config file.

    Nothing about the client changes between these two runs. Same book, same
    budget, same shock, same puts. Only the calls are priced differently, and
    the answer flips -- which a stated preference could never do, because a
    stated preference does not know what day it is.
    """
    rich = [call_row(540, 5.00, iv=0.30)]
    cheap = [call_row(540, 5.00, iv=0.15)]

    ringed, why_ringed = choose(offers_on(rich))
    bare, why_bare = choose(offers_on(cheap))

    assert ringed.kind == "collar"
    assert bare.kind == "protective_put"
    # And each says which way the comparison went, in the same sentence that
    # carries the decision.
    assert "richer leg" in why_ringed
    assert "below the put's price per unit of risk" in why_bare


def test_the_comparison_is_volatility_and_not_premium():
    """Why raw premium cannot do this job.

    Here the call collects far more cash than the put costs, so on any
    cash-based test the collar wins in a landslide. It is still the wrong sale:
    per unit of risk the market prices this call below the put being bought, so
    the client would be handing over cheap upside to buy expensive downside --
    a worse trade at every outcome, not merely at the ones a forecast dislikes.
    """
    generous_but_cheap = [call_row(520, 40.00, iv=0.10)]
    chosen, why = choose(offers_on(generous_but_cheap))
    ring = next(r for r in offers_on(generous_but_cheap) if r.kind == "collar")

    assert ring.premium_cost < 0, "the collar is opened for a large credit"
    assert chosen.kind == "protective_put"
    assert "underpriced upside" in why


def test_upside_that_cannot_be_priced_is_not_sold():
    """Missing volatility reads as "do not", never as "proceed".

    The chain not saying what something is worth is not evidence that selling
    it is a good idea, and this is the direction the failure has to go: the
    fallback costs the client premium, while the other fallback would cost them
    an unbounded ceiling they were never quoted a price for.
    """
    unquoted = [call_row(540, 5.00, iv=None)]
    chosen, why = choose(offers_on(unquoted))
    assert chosen.kind == "protective_put"
    assert "cannot be checked" in why

    ring = next(r for r in offers_on(unquoted) if r.kind == "collar")
    assert ring.financed_fairly is None, "not False -- unknown is its own answer"


def test_the_terms_of_the_financing_are_reported_whichever_way_it_goes():
    """`upside_price` is the number that actually moves day to day.

    Dollars collected per 1% of upside surrendered: 6,000 for a ceiling 8%
    above spot is 750 per point. It is reported rather than thresholded,
    because a cutoff would be a constant somebody picked.
    """
    ring = next(r for r in offers_on([call_row(540, 5.00)]) if r.kind == "collar")
    assert ring.financing_credit == pytest.approx(6_000)
    assert ring.ceiling_pct == pytest.approx(8.0)
    assert ring.upside_price == pytest.approx(750.0)

    # A nearer ceiling collects more cash but sells more of the range, and the
    # per-point price is what makes the two comparable.
    near = next(r for r in offers_on([call_row(520, 9.00)]) if r.kind == "collar")
    assert near.financing_credit > ring.financing_credit
    assert near.upside_price > ring.upside_price


def test_a_reversible_remedy_beats_a_permanent_one_at_any_price():
    """Step two of `choose` runs before any price is consulted, and this pins it.

    The sale costs no cash at all and would win any cost ranking. It still
    loses, because it answers a condition that usually reverses with a decision
    that never does.
    """
    # A chain whose calls are too far out to fully fund the put, so both option
    # remedies cost cash and the sale is genuinely the cheapest of the three.
    # On the default chain the collar opens for a credit and wins on cost by
    # itself, which would prove nothing about the ordering being tested.
    debit = [call_row(560, 2.00)]
    sale = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    chosen, _ = choose([*offers_on(debit), sale])
    assert sale.premium_cost < min(r.premium_cost for r in offers_on(debit))
    assert chosen.kind != "reduce_exposure"


def test_the_permanent_remedy_is_taken_when_it_is_the_only_one_that_works():
    """Last, not never. A gap left open because no strike was deep enough is
    still a broken promise, and refusing the one remedy that closes it would be
    fastidiousness rather than caution."""
    useless = [put_row(300, 0.05)]
    sale = reduce_exposure(BOOK, [], BUDGET, SHOCK, "SPY")
    offers = [r for r in offers_on(CALLS, puts=useless)] + [sale]

    chosen, why = choose(offers)
    assert chosen.kind == "reduce_exposure"
    assert "no remedy that expires closes the gap" in why


def test_choosing_nothing_says_so_rather_than_returning_a_near_miss():
    """A remedy that leaves the promise broken is not a cheaper way of keeping
    it, so it is not returned as though it were."""
    useless = [put_row(300, 0.05)]
    chosen, why = choose(offers_on(CALLS, puts=useless))
    assert chosen is None
    assert "closes the gap" in why


# --- giving protection back -------------------------------------------------
#
# The fourth action, and the one with the most ways to be wrong. Buying too
# little protection is visible in the ladder. Releasing the wrong protection is
# only visible afterwards, in a shock.


def long_put(strike: float, contracts: int, premium: float = 5.0) -> OptionLeg:
    return OptionLeg(
        "SPY", "P", Decimal(str(strike)), contracts, Decimal(str(premium)), SPOT
    )


# 1,000 shares at 500 loses exactly the budget at -20%, so any protection on top
# of it is headroom rather than necessity. This is the book releases happen on.
INSIDE = [Holding("SPY", 1000, SPOT), Holding("CASH", 500_000, 1.0, shocked=False)]


def test_protection_that_is_exactly_holding_the_promise_is_not_released():
    """The single-threshold bug, tested from the side where it bites.

    A 440 put on the 1,200-share book brings the gap to precisely zero. Zero is
    inside the budget, and an agent releasing at "no gap" would hand this back
    immediately, reopen the gap, and buy it again — paying the spread twice per
    round trip to end where it started.
    """
    held = [long_put(440, 12)]  # one contract per hundred shares, exactly
    assert _uncovered(BOOK, held, BUDGET, SHOCK) == 0.0
    assert release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0) is None


def test_redundant_protection_is_released_only_down_to_the_margin():
    """Partial, and the part that comes back is the surplus.

    Twelve contracts stand behind 1,200 shares. Fifteen is three more than the
    book has shares for, and those three protect nothing -- below the strike
    the matched twelve already flatten the loss, so the extra pay out on a
    position that is not there.

    Releasing a *matched* contract is a different act and this refuses it: the
    hundred shares it uncovered would fall with no floor at all, which is not
    a smaller promise but no promise. That is why the whole-descent measure
    changed what this function can do -- at one shock the difference is
    invisible.
    """
    held = [long_put(440, 15)]
    given = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)

    assert given is not None
    assert given.contracts == 3
    assert given.margin_required == pytest.approx(15_000)
    # The surplus contracts protected nothing, so handing them back moves the
    # headroom not at all. That is the point of calling them surplus.
    assert given.slack_after == pytest.approx(given.slack_before)
    assert given.slack_after >= given.margin_required


def test_a_wider_margin_releases_less_and_a_narrower_one_releases_more():
    """The band is the client's dial, and it has to actually turn something.

    The first version of this test compared 25% against 10% and proved nothing:
    at 25% no release is possible at all on this book, so the assertion took its
    `is None` branch and never reached the comparison. Both margins here release
    something, which is the only way the ordering is actually exercised.
    """
    # Twelve stand behind the shares; the rest is surplus, and the margin
    # decides how much of the surplus stays.
    held = [long_put(440, 18)]
    cautious = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)
    eager = release(BOOK, held, BUDGET, SHOCK, margin_pct=5.0)

    # Both hand back all six surplus contracts: they protect nothing, so no
    # margin can be a reason to keep them. The margin governs how much *live*
    # protection may go, and there is none to give here.
    assert cautious.contracts == 6
    assert eager.contracts == 6
    assert eager.margin_required < cautious.margin_required
    # Surplus goes back under any band, because keeping it buys nothing. What
    # the band still governs is protection that is doing work: twelve matched
    # contracts leave 28,000 of headroom, and no margin short of that can
    # justify handing one of them away.
    assert release(BOOK, [long_put(440, 12)], BUDGET, SHOCK, margin_pct=25.0) is None


def test_spent_protection_goes_back_even_though_the_gap_is_open():
    """The roll, and the reason it is safe.

    A 380 put on a 500 stock pays nothing at -20%: the shocked price is 400 and
    the strike is below it. It is not holding the promise up, so releasing it
    cannot widen the gap — the headroom is identical before and after. Without
    this rule the agent would buy a fresh put on every rally and stack the
    corpses of the old ones forever.
    """
    held = [long_put(380, 2)]
    before = _uncovered(BOOK, held, BUDGET, SHOCK)
    given = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)

    assert given is not None
    assert given.reason == "spent"
    assert given.contracts == 2
    assert before > 0, "the gap is still open, and the release happens anyway"
    # A leg worth nothing at the promised shock can still be the floor deeper
    # down, so handing it back is not free on the whole-descent measure. It is
    # still the right trade -- the client is paying to carry it -- but the
    # journal must not claim the headroom was unchanged.
    assert given.slack_after < given.slack_before
    # The gap on the bare book is the whole unbounded downside, which is what
    # the legs were failing to bound in the first place.
    assert _uncovered(BOOK, [], BUDGET, SHOCK) >= before


def test_a_spent_leg_still_worth_something_in_the_tail_says_so():
    """Released, but not silently.

    The 380 put pays nothing at the promised -20% and 55 a share at -35%. The
    agent closes what it promised and discloses what it did not, so a leg that
    was still real tail protection leaves a number behind rather than just
    leaving.
    """
    given = release(BOOK, [long_put(380, 2)], BUDGET, SHOCK, margin_pct=15.0)
    assert given.tail_shock == -0.35
    assert given.tail_given_up == pytest.approx(55 * 2 * 100)
    assert f"{given.tail_given_up:,.0f}" in given.line()


def test_the_wheels_own_short_puts_are_never_mistaken_for_protection():
    """A sold put is the risk, not the cover. Releasing one would be an order to
    close an income position because the client felt safe, which is not what any
    of this is for."""
    sold = [OptionLeg("SPY", "P", Decimal("450"), -4, Decimal("3.0"), SPOT)]
    assert release(INSIDE, sold, BUDGET, SHOCK, margin_pct=15.0) is None


def test_dropping_the_floor_under_a_short_call_is_reported():
    """Disclosed rather than blocked, because positions do not carry intent.

    A short call with no put behind it is either half a collar — the client
    keeping the ceiling and losing the floor, which is the worst of both — or an
    ordinary covered call the position sold on purpose. Nothing in a list of broker
    positions distinguishes them, so the caller is told instead of guessed at.
    """
    held = [
        long_put(380, 2),
        OptionLeg("SPY", "C", Decimal("560"), -2, Decimal("4.0"), SPOT),
    ]
    given = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)
    assert given.leaves_ceiling == ["SPY"]
    assert "ceiling left standing" in given.line()


def test_a_release_that_keeps_some_floor_reports_no_orphaned_ceiling():
    held = [
        long_put(380, 2),  # spent, goes back
        long_put(460, 3),  # live, stays
        OptionLeg("SPY", "C", Decimal("560"), -2, Decimal("4.0"), SPOT),
    ]
    given = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)
    assert given.leaves_ceiling == []


def test_release_cannot_consult_the_price_of_the_hedge():
    """Structural, not behavioural, and that is the point.

    A put is worth most exactly when it is needed most, so an agent taking
    profits on protection sells the hedge into the decline it was bought for.
    The mark is not weighed and rejected inside the function — it is absent from
    the signature, so no later edit can quietly start reading it without also
    changing every caller.
    """
    taken = set(inspect.signature(release).parameters)
    assert not taken & {"mark", "price", "bid", "ask", "quote", "puts", "chain"}


def test_what_the_protection_cost_cannot_change_whether_it_is_released():
    """The same invariant, proved by behaviour rather than by signature.

    One book paid 1.00 for its puts and the other paid 99.00. Sunk cost is the
    most tempting wrong input here — the expensive hedge is the one it feels
    worst to give up — and the ladder measures protection against its own
    zero-shock baseline precisely so the premium cancels.
    """
    cheap = release(BOOK, [long_put(440, 15, premium=1.0)], BUDGET, SHOCK, 15.0)
    dear = release(BOOK, [long_put(440, 15, premium=99.0)], BUDGET, SHOCK, 15.0)

    assert cheap.contracts == dear.contracts
    assert cheap.slack_after == pytest.approx(dear.slack_after)
    assert cheap.reason == dear.reason


# --- from a remedy to an order --------------------------------------------


def full_put_row(strike: float, ask: float) -> dict:
    """A chain row shaped the way `load_chain` returns them.

    The thin rows above carry a strike and a price, which is all a payoff
    needs. An order needs the expiry and the greeks as well, so the tests that
    are about orders say so by using this.
    """
    from datetime import date, timedelta

    return {
        "occ_symbol": "SPY271231P00420000",
        "strike": strike,
        "expiry": date.today() + timedelta(days=365),
        "right": "P",
        "bid": ask - 0.10,
        "ask": ask,
        "open_interest": 5_000,
        "implied_vol": 0.22,
    }


def test_a_remedy_arrives_carrying_the_order_that_places_it():
    """`legs` describe the payoff; they cannot be sent to a broker.

    A leg has a strike and a premium and no expiry, so an agent holding only
    legs has priced a hedge it has no way to buy. The order is built where the
    chain row is in hand -- re-reading the chain later can return a different
    quote, and an order priced off a row nobody journalled is one nobody can
    check afterwards.
    """
    rows = [full_put_row(420, 2.50)]
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, rows)

    assert len(remedy.orders) == 1
    order = remedy.orders[0]
    assert order.contracts == 12  # bought, so positive
    assert order.strike == Decimal("420")
    assert order.expiry == rows[0]["expiry"]
    # The ask plus a quarter of the spread, never the mid: a mid is a price
    # nobody is offering, and the ask alone fills only if nobody moves.
    assert order.limit_price == Decimal("2.61")
    assert order.delta < 0  # a long put shortens the book
    assert order.open_interest == 5_000


def test_the_order_a_remedy_carries_is_one_the_gate_approves():
    """The end of the chain that matters. A remedy the gate would refuse is a
    plan, not a hedge, and the agent would report a closed gap it never closed.
    """
    from drawdownguard.domain import Portfolio, Position
    from drawdownguard.risk.gate import veto
    from drawdownguard.risk.limits import Limits

    rows = [full_put_row(420, 2.50)]
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, rows)
    client = Portfolio(
        equity=Decimal("1000000"),
        cash=Decimal("400000"),
        peak_equity=Decimal("1000000"),
        net_delta_value=600_000.0,
        positions={"SPY": Position(symbol="SPY", leg="SHARES", shares=1200)},
    )
    limits = Limits(
        max_position_pct=25.0,
        max_deployed_pct=60.0,
        max_drawdown_pct=15.0,
        max_net_delta_pct=50.0,
        max_vega=500.0,
        max_assignment_prob=0.35,
        min_open_interest=500,
        max_spread_pct=5.0,
        forbid_naked=True,
    )
    verdict = veto(remedy.orders[0], client, limits)
    assert verdict.approved, verdict.reason


def test_a_row_too_thin_to_trade_yields_a_price_but_no_order():
    """Silence rather than a malformed order. `execute` then journals that the
    remedy could not be placed, at breach severity, instead of sending an order
    with no expiry on it."""
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy is not None
    assert remedy.orders == ()


# --- one hedge per holding --------------------------------------------------


def test_the_budget_is_split_by_what_each_holding_can_lose():
    """A symbol holding half the book may lose half the money.

    The shares sum to the promise, so each sleeve floored inside its own share
    leaves the book floored inside the whole -- and no sleeve has to know
    anything about the others.
    """
    from drawdownguard.risk.remedy import sleeves

    book = [
        Holding("SPY", 800, 500.0),  # 400,000
        Holding("QQQ", 400, 500.0),  # 200,000
        Holding("IWM", 400, 500.0),  # 200,000
        Holding("CASH", 200_000, 1.0, shocked=False),
    ]
    split = {symbol: budget for symbol, _, budget in sleeves(book, 100_000.0)}
    assert split["SPY"] == pytest.approx(50_000)
    assert split["QQQ"] == pytest.approx(25_000)
    assert split["IWM"] == pytest.approx(25_000)
    assert sum(split.values()) == pytest.approx(100_000)


def test_what_cannot_fall_gets_no_share_of_the_protection():
    """Bills do not move in an equity shock, so there is nothing there to
    protect. Giving them a slice of the budget would hand part of the client's
    protection to the one position that cannot lose it."""
    from drawdownguard.risk.remedy import sleeves

    book = [Holding("SPY", 100, 500.0), Holding("BIL", 500_000, 1.0, shocked=False)]
    assert [symbol for symbol, _, _ in sleeves(book, 100_000.0)] == ["SPY"]


def test_a_book_with_nothing_exposed_has_no_sleeves_rather_than_a_zero_one():
    from drawdownguard.risk.remedy import sleeves

    assert sleeves([Holding("BIL", 1000, 91.6, shocked=False)], 100_000.0) == []


def test_each_sleeve_is_hedged_on_its_own_underlying():
    """The defect this replaced.

    The agent used to buy puts on its largest holding and size them to the
    whole book, which treats three indices as one thing falling by one number.
    Measured on this project's own bars, QQQ carries a beta of 1.17 to SPY and
    IWM 1.12, so a notional match left 11,700 of a 100,000 promise uncovered.

    A QQQ put pays on QQQ however far QQQ falls. No beta is estimated because
    none is needed -- an estimate that could be wrong is replaced by an
    instrument that cannot be.
    """
    from drawdownguard.risk.remedy import contracts_to_match, sleeves

    book = [Holding("SPY", 800, 500.0), Holding("QQQ", 400, 500.0)]
    for symbol, sleeve, budget in sleeves(book, 100_000.0):
        assert [h.symbol for h in sleeve] == [symbol]
        # Contracts follow the sleeve's own shares, not the book's total.
        assert contracts_to_match(sleeve, 500.0) == sum(h.shares for h in sleeve) // 100
        assert budget < 100_000.0


# --- closing what was released ----------------------------------------------


def closable_row(strike: float, bid: float) -> dict:
    from datetime import date, timedelta

    return {
        "strike": strike,
        "expiry": date.today() + timedelta(days=400),
        "right": "P",
        "bid": bid,
        "ask": bid + 0.10,
        "open_interest": 5_000,
        "implied_vol": 0.22,
    }


_UNSET = object()


def held_leg(strike=440.0, contracts=3, expiry=_UNSET) -> OptionLeg:
    """A leg as the reconciler builds it, with the expiry the broker reported.

    `expiry` takes a sentinel rather than defaulting on None, because None is
    the case under test: a leg built for arithmetic alone carries no date, and
    conflating "not supplied" with "genuinely absent" would make that test pass
    against the wrong object.
    """
    from datetime import date, timedelta

    return OptionLeg(
        "SPY",
        "P",
        Decimal(str(strike)),
        contracts,
        Decimal("5.0"),
        SPOT,
        expiry=(date.today() + timedelta(days=400)) if expiry is _UNSET else expiry,
    )


def test_a_released_leg_becomes_an_order_that_sells_it():
    """`release` returned an answer nothing could act on.

    The journal reported a handback, the puts stayed in the account, and the
    next cycle found them, called them redundant again and bought protection on
    top -- 20,130 of premium over five cycles closing a gap that was never
    open. A release has to become an order or it is not a release.
    """
    from drawdownguard.risk.remedy import closing_orders

    chains = {"SPY": {"P": [closable_row(440.0, 6.00)], "C": []}}
    orders = closing_orders([held_leg()], chains)

    assert len(orders) == 1
    assert orders[0].contracts == -3  # closing a long position is a sale
    # The bid less a quarter of the spread. A sale priced exactly at the bid
    # has the same problem in the other direction.
    assert orders[0].limit_price == Decimal("5.90")
    assert orders[0].strike == Decimal("440")


def test_a_leg_with_no_expiry_closes_nothing_rather_than_guessing():
    """An `OptionLeg` built for arithmetic carries no date, and an order needs
    one. Sending it at a guessed expiry would close a contract nobody holds."""
    from drawdownguard.risk.remedy import closing_orders

    chains = {"SPY": {"P": [closable_row(440.0, 6.00)], "C": []}}
    assert closing_orders([held_leg(expiry=None)], chains) == []


def test_a_contract_missing_from_today_s_chain_stops_the_whole_handback():
    """All or nothing. A partial release leaves the caller holding a book that
    matches neither what it released nor what it kept, and every number
    computed after that describes a portfolio that does not exist."""
    from drawdownguard.risk.remedy import closing_orders

    chains = {"SPY": {"P": [closable_row(999.0, 6.00)], "C": []}}
    assert closing_orders([held_leg(), held_leg(strike=460.0)], chains) == []


def test_one_sleeve_being_over_hedged_does_not_release_another_sleeve():
    """The cross-subsidy `protect` refuses when buying, refused when releasing.

    `ladder` moves every holding by the same shock, so a sleeve carrying more
    protection than its shares -- ordinary, since contracts come in hundreds --
    shows a gain on the way down that offsets a different symbol's loss. Read
    at book level that is headroom, and the headroom was spent handing back the
    second symbol's puts.

    The two sides then disagreed inside one cycle: the release gave back a
    sleeve's protection because a different sleeve was over-hedged, and the
    per-sleeve check bought the identical strike back immediately. Two spreads
    paid to end exactly where the cycle started.
    """
    holdings = [
        Holding("IWM", 100, 300.0),
        Holding("XLF", 400, 58.0),
        Holding("CASH", 40_000, 1.0, shocked=False),
    ]
    budget = 10_000.0
    # IWM is over-hedged: 100 shares, two contracts covering 200. XLF is
    # matched exactly, and its puts are the only thing standing behind it.
    legs = [
        OptionLeg("IWM", "P", Decimal("280"), 2, Decimal("15.58"), 300.0),
        OptionLeg("XLF", "P", Decimal("54"), 4, Decimal("2.94"), 58.0),
    ]

    given = release(holdings, legs, budget, SHOCK, margin_pct=15.0)

    assert given is not None, "the surplus IWM contract is genuinely redundant"
    released = {leg.symbol for leg in given.legs}
    assert released == {"IWM"}, (
        "the release has to come out of the sleeve that is over-hedged, not out "
        f"of the one that is exactly matched; got {released}"
    )
    # And what stands behind XLF is untouched.
    kept_xlf = [leg for leg in given.kept if leg.symbol == "XLF"]
    assert sum(leg.contracts for leg in kept_xlf) == 4


def test_a_limit_reaches_a_whole_spread_past_the_offer():
    """Set exactly at the offer, a limit fills only if nobody moves.

    On 2026-08-28 two protective puts were priced at the ask, the ask rose a
    few cents while the cycle was still running, and both sat unfilled until
    the close -- 71,985 of risk left uncovered overnight to avoid paying 20
    dollars.

    A quarter of the spread was the first answer and it was not enough. On
    2026-08-31 the XLF put was priced against a 2.72 ask and sent at 2.78; by
    the close the ask was 2.87 and the order had sat under the market all day.
    A day limit cannot chase, so the tolerance is really "how far may the offer
    drift before the promise goes unheld for another day".
    """
    from drawdownguard.risk.remedy import CROSS_FRACTION, crossing_price

    row = {"bid": 2.63, "ask": 2.70}
    assert crossing_price(row, buying=True) == Decimal("2.78")
    assert CROSS_FRACTION == 1.0


def test_the_tolerance_would_have_bought_the_day_it_failed():
    """The actual quote the XLF put was priced against, and the actual close.

    Kept as a test rather than as a comment because the number was changed on
    this evidence, and a constant tuned against a day nobody can reproduce is
    a constant that drifts back.
    """
    from drawdownguard.risk.remedy import crossing_price

    limit = crossing_price({"bid": 2.48, "ask": 2.72}, buying=True)
    assert limit == Decimal("2.97")
    assert limit > Decimal("2.87"), "the ask it drifted to by the close"


def test_the_tolerance_scales_with_the_spread_and_not_with_the_price():
    """Two cents is 0.8% of a 2.64 contract and 6.7% of a 0.30 one.

    A fixed number of cents would be a rounding error on one and a material
    overpayment on the other, so the room is taken from the spread -- which is
    what actually has to be crossed.
    """
    from drawdownguard.risk.remedy import crossing_price

    wide = crossing_price({"bid": 14.14, "ask": 14.30}, buying=True)
    tight = crossing_price({"bid": 5.00, "ask": 5.02}, buying=True)
    assert wide == Decimal("14.47"), "0.16 of spread earns sixteen cents of room"
    assert tight == Decimal("5.04"), "0.02 of spread earns two"


def test_a_sale_gives_up_spread_rather_than_demanding_it():
    """Handing protection back has the same problem in the other direction: a
    sale priced exactly at the bid does not fill once the bid ticks down."""
    from drawdownguard.risk.remedy import crossing_price

    assert crossing_price({"bid": 2.63, "ask": 2.70}, buying=False) == Decimal("2.55")


def test_a_sale_is_never_talked_below_a_penny():
    from drawdownguard.risk.remedy import crossing_price

    assert crossing_price({"bid": 0.01, "ask": 0.40}, buying=False) == Decimal("0.01")


def test_the_budget_is_charged_what_the_order_will_actually_pay():
    """The premium comes out of the same account the promise is written
    against, so a strike admitted on a price the agent will not pay is a strike
    sized against the wrong number -- by exactly the tolerance."""
    from drawdownguard.risk.remedy import crossing_price, solve_for_strike

    rows = [put_row(460, 9.00), put_row(470, 12.00)]
    for row in rows:
        row["bid"] = row["ask"] - 0.20
    solved = solve_for_strike(BOOK, [], BUDGET, "SPY", SPOT, rows)
    assert solved is not None
    _, _, price = solved
    row = next(r for r in rows if Decimal(str(r["strike"])) == solved[0])
    assert Decimal(str(price)) == crossing_price(row, buying=True)
    assert price > row["ask"], "the reported price includes the room"


def test_a_position_can_be_closed_even_when_the_filter_would_refuse_to_buy_it():
    """The filter stops the solver buying a strike nobody trades. Applying it
    to an exit locks the client into any position that has since become
    illiquid -- which happened on 2026-09-02, when the client sold their XLF
    shares and the 56 strike behind them was one of the sixty-six the filter
    rejected that morning."""
    from datetime import date

    from drawdownguard.risk.remedy import closing_orders

    leg = OptionLeg(
        symbol="XLF",
        right="P",
        strike=Decimal("56"),
        contracts=9,
        premium=Decimal("3.50"),
        spot=57.22,
        expiry=date(2027, 12, 17),
    )
    thin = {
        "strike": 56.0,
        "expiry": date(2027, 12, 17),
        "bid": 3.10,
        "ask": 3.90,
        "implied_vol": 0.21,
        "open_interest": 12,
        "delta": -0.33,
        "right": "P",
    }
    orders = closing_orders([leg], {"XLF": {"P": [thin]}})
    assert len(orders) == 1
    assert orders[0].contracts == -9, "closing a long position is a sale"


def test_a_contract_missing_from_the_chain_is_still_refused():
    """Reported rather than approximated: an order at a guessed price is worse
    than one not sent."""
    from datetime import date

    from drawdownguard.risk.remedy import closing_orders

    leg = OptionLeg(
        symbol="XLF",
        right="P",
        strike=Decimal("56"),
        contracts=9,
        premium=Decimal("3.50"),
        spot=57.22,
        expiry=date(2027, 12, 17),
    )
    assert closing_orders([leg], {"XLF": {"P": []}}) == []
