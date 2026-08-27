"""Three remedies, priced in three currencies, none of them ranked.

The load-bearing tests are the ones that keep the comparison honest: every
remedy's `gap_after` is the real ladder rather than an estimate, the collar
cannot sell a call it has no shares for, and selling stock is reported as
costing upside rather than as costing nothing.
"""

import inspect
from decimal import Decimal

import pytest

from drawdownguard.risk.remedy import (
    _gap,
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
    assert rung.gap == pytest.approx(20_000)


# --- protective put ---------------------------------------------------------


def test_a_protective_put_closes_the_gap_and_says_what_it_cost():
    remedy = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    assert remedy is not None
    assert remedy.closes_the_gap
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
    assert remedy.premium_cost == pytest.approx(3_000)


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

    assert (put_only.premium_cost, put_only.forgone_upside) == (3_000.0, 0.0)
    assert ringed.premium_cost < put_only.premium_cost
    assert ringed.forgone_upside > 0
    assert sold.premium_cost == 0.0
    assert sold.forgone_upside > 0

    # All three close the same gap. Nothing about that makes them equivalent.
    for remedy in (put_only, ringed, sold):
        assert remedy.closes_the_gap
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
    the same gap for less cash and reports a smaller number: the 560 call
    brings in 2,400 of the 3,000, leaving 600, which is 30 per thousand.
    """
    put_only = protective_put(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS)
    ringed = collar(BOOK, [], BUDGET, SHOCK, "SPY", SPOT, PUTS, THIN_CALLS)

    assert put_only.gap_closed == pytest.approx(20_000)
    assert put_only.cash_per_1k == pytest.approx(150.0)
    assert ringed.cash_per_1k == pytest.approx(30.0)


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
    held = [long_put(440, 5)]
    assert _gap(BOOK, held, BUDGET, SHOCK) == 0.0
    assert release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0) is None


def test_redundant_protection_is_released_only_down_to_the_margin():
    """Partial, because the margin is a quantity and not a switch.

    Four contracts of the five pay 16,000 at the shock, which is 1,000 more
    headroom than the 15,000 the margin demands. Releasing a second contract
    would drop it to 12,000 and break the band, so exactly one goes back.
    """
    held = [long_put(440, 5)]
    given = release(INSIDE, held, BUDGET, SHOCK, margin_pct=15.0)

    assert given is not None
    assert given.reason == "redundant"
    assert given.contracts == 1
    assert given.margin_required == pytest.approx(15_000)
    assert given.slack_after == pytest.approx(16_000)
    assert given.slack_after >= given.margin_required


def test_a_wider_margin_releases_less_and_a_narrower_one_releases_more():
    """The band is the client's dial, and it has to actually turn something.

    The first version of this test compared 25% against 10% and proved nothing:
    at 25% no release is possible at all on this book, so the assertion took its
    `is None` branch and never reached the comparison. Both margins here release
    something, which is the only way the ordering is actually exercised.
    """
    held = [long_put(440, 5)]
    cautious = release(INSIDE, held, BUDGET, SHOCK, margin_pct=15.0)
    eager = release(INSIDE, held, BUDGET, SHOCK, margin_pct=5.0)

    assert cautious.contracts == 1
    assert eager.contracts == 3
    # And the conservative mandate's 25% band cannot let go of any of it: five
    # contracts buy 20,000 of headroom and the band demands 25,000.
    assert release(INSIDE, held, BUDGET, SHOCK, margin_pct=25.0) is None


def test_spent_protection_goes_back_even_though_the_gap_is_open():
    """The roll, and the reason it is safe.

    A 380 put on a 500 stock pays nothing at -20%: the shocked price is 400 and
    the strike is below it. It is not holding the promise up, so releasing it
    cannot widen the gap — the headroom is identical before and after. Without
    this rule the agent would buy a fresh put on every rally and stack the
    corpses of the old ones forever.
    """
    held = [long_put(380, 2)]
    before = _gap(BOOK, held, BUDGET, SHOCK)
    given = release(BOOK, held, BUDGET, SHOCK, margin_pct=15.0)

    assert given is not None
    assert given.reason == "spent"
    assert given.contracts == 2
    assert before > 0, "the gap is still open, and the release happens anyway"
    assert given.slack_after == pytest.approx(given.slack_before)
    assert _gap(BOOK, [], BUDGET, SHOCK) == pytest.approx(before)


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
    ordinary covered call the wheel sold on purpose. Nothing in a list of broker
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
    cheap = release(INSIDE, [long_put(440, 5, premium=1.0)], BUDGET, SHOCK, 15.0)
    dear = release(INSIDE, [long_put(440, 5, premium=99.0)], BUDGET, SHOCK, 15.0)

    assert cheap.contracts == dear.contracts
    assert cheap.slack_after == pytest.approx(dear.slack_after)
    assert cheap.reason == dear.reason
