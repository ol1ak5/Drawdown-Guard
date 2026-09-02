from datetime import date
from decimal import Decimal

import pytest

from drawdownguard.domain import OpenContract, Portfolio, Position, ProposedOrder
from drawdownguard.risk.gate import veto
from drawdownguard.risk.limits import Limits

LIMITS = Limits(
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


def order(**overrides) -> ProposedOrder:
    values = {
        "symbol": "SPY",
        "right": "P",
        "strike": Decimal("560"),
        "expiry": date(2026, 8, 28),
        "contracts": -1,
        "limit_price": Decimal("2.35"),
        "delta": -0.30,
        "vega": 40.0,
        "assignment_prob": 0.25,
        "open_interest": 5000,
        "spread_pct": 0.5,
        "spot": 600.0,
    }
    values.update(overrides)
    return ProposedOrder(**values)


def portfolio(**overrides) -> Portfolio:
    values = {
        "equity": Decimal("300000"),
        "cash": Decimal("300000"),
        "peak_equity": Decimal("300000"),
        "deployed": Decimal("0"),
        "net_delta": 0.0,
        "vega": 0.0,
        "positions": {"SPY": Position(symbol="SPY")},
    }
    values.update(overrides)
    return Portfolio(**values)


def test_a_clean_order_is_approved():
    assert veto(order(), portfolio(), LIMITS).approved is True


def test_naked_call_is_rejected_when_no_shares_are_held():
    verdict = veto(order(right="C"), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "naked" in verdict.reason.lower()


def test_covered_call_is_approved_when_shares_are_held():
    held = portfolio(
        positions={"SPY": Position(symbol="SPY", leg="SHARES", shares=100)}
    )
    assert veto(order(right="C", delta=0.30), held, LIMITS).approved is True


def test_cash_secured_put_is_rejected_without_the_cash():
    # one contract at strike 560 needs 56,000 of cash
    broke = portfolio(cash=Decimal("10000"))
    verdict = veto(order(), broke, LIMITS)
    assert verdict.approved is False
    assert "cash" in verdict.reason.lower()


def test_drawdown_beyond_the_limit_rejects_everything():
    drawn = portfolio(equity=Decimal("250000"), peak_equity=Decimal("300000"))
    verdict = veto(order(), drawn, LIMITS)  # 16.7% > 15%
    assert verdict.approved is False
    assert "drawdown" in verdict.reason.lower()


def test_position_concentration_limit():
    # 56,000 collateral on 200,000 equity = 28% > 25%.
    # peak_equity tracks equity so the drawdown check stays silent and the
    # concentration check is the one under test.
    small = portfolio(
        equity=Decimal("200000"),
        cash=Decimal("200000"),
        peak_equity=Decimal("200000"),
    )
    verdict = veto(order(), small, LIMITS)
    assert verdict.approved is False
    assert "position" in verdict.reason.lower()


def test_total_deployed_limit():
    loaded = portfolio(deployed=Decimal("160000"))  # +56,000 = 72% > 60%
    verdict = veto(order(), loaded, LIMITS)
    assert verdict.approved is False
    assert "deployed" in verdict.reason.lower()


def test_net_delta_band():
    """Equity 300,000, band 50% = 150,000. This order adds 18,000."""
    skewed = portfolio(net_delta_value=140_000.0)
    verdict = veto(order(delta=-0.30), skewed, LIMITS)
    assert verdict.approved is False
    assert "directional" in verdict.reason.lower()


def test_short_put_contributes_positive_delta():
    """A short put is a bullish position, so it offsets a short book."""
    skewed = portfolio(net_delta_value=-140_000.0)
    assert veto(order(delta=-0.30), skewed, LIMITS).approved is True


def test_the_band_treats_equal_exposure_equally_across_instruments():
    """The reason the unit changed.

    A cheap instrument and an expensive one, carrying the same dollar risk,
    must get the same answer. Under the old share-equivalent band they did not:
    at a quarter of a 1,000,000 account one position is 300 shares of SPY or
    1,000 of IWM, so a band in shares was strict on the cheap one and
    permissive on the expensive one for identical exposure.
    """
    # 1 contract of a 0.30-delta put: 30 share equivalents either way, but
    # 18,000 dollars at 600 a share and 18,000 at 60 a share with ten contracts.
    expensive = order(delta=-0.30, contracts=-1, spot=600.0)
    cheap = order(delta=-0.30, contracts=-10, spot=60.0)
    assert expensive.delta_value == cheap.delta_value

    book = portfolio(net_delta_value=140_000.0)
    assert veto(expensive, book, LIMITS).approved is False
    assert veto(cheap, book, LIMITS).approved is False


def test_the_band_scales_with_the_account():
    """A percentage limit means the same thing at every account size.

    The old absolute share count did not: 150 shares was most of a small
    account and a rounding error in a large one, under the same configuration.
    """
    small = portfolio(equity=Decimal("100000"), net_delta_value=45_000.0)
    large = portfolio(equity=Decimal("1000000"), net_delta_value=450_000.0)
    proposed = order(delta=-0.30, contracts=-1, spot=600.0)  # +18,000
    assert veto(proposed, small, LIMITS).approved is False  # 63k of 100k = 63%
    assert veto(proposed, large, LIMITS).approved is True  # 468k of 1M = 47%


def test_vega_budget():
    loaded = portfolio(vega=480.0)
    verdict = veto(order(vega=40.0), loaded, LIMITS)
    assert verdict.approved is False
    assert "vega" in verdict.reason.lower()


def test_assignment_probability_budget():
    verdict = veto(order(assignment_prob=0.55), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "assignment" in verdict.reason.lower()


def test_open_interest_floor():
    verdict = veto(order(open_interest=50), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "open interest" in verdict.reason.lower()


def test_spread_ceiling():
    verdict = veto(order(spread_pct=12.0), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "spread" in verdict.reason.lower()


# --- buying protection ------------------------------------------------------
#
# The gate was written for a strategy that only ever sold options, so it
# rejected every purchase. An agent whose job is to buy protection could not
# place a single one of its own orders. What follows is the boundary that
# replaced that rule: a purchase is admitted when it demonstrably reduces
# risk, and refused when it is a directional bet wearing a hedge's clothes.


def holding(shares: int = 1000, contracts=None, **overrides) -> Portfolio:
    """A client who owns shares, which is what makes a put protective.

    Coherent on purpose: 1,000 shares at 600 is 600,000 of exposure, so
    `net_delta_value` says 600,000 and the equity is large enough to hold it
    without leverage. Left at the default zero, the fixture would describe a
    portfolio whose shares have no delta, and every test below would be
    measuring a book that cannot exist.
    """
    state = Position(
        symbol="SPY", leg="SHARES", shares=shares, contracts=contracts or []
    )
    values = {
        "equity": Decimal("1000000"),
        "cash": Decimal("400000"),
        "peak_equity": Decimal("1000000"),
        "net_delta_value": float(shares) * 600.0,
        "positions": {"SPY": state},
    }
    values.update(overrides)
    return portfolio(**values)


def test_a_put_against_shares_the_client_holds_is_approved():
    """The agent's entire purpose, and the old gate refused it."""
    assert veto(order(contracts=10), holding(), LIMITS).approved is True


def test_a_put_on_something_the_client_does_not_own_is_refused():
    """Bought against nothing, a put is not insurance -- it is a short position
    on the market with extra steps. The one trade this agent must never place,
    because its whole claim is that it never takes a view."""
    verdict = veto(order(contracts=10), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "does not hold" in verdict.reason.lower()


def test_buying_a_call_to_open_is_refused():
    """Upside bought with cash is leverage. Nothing about the mandate asks for
    it, and no shortfall in the downside budget can be closed with one."""
    verdict = veto(order(right="C", contracts=10, delta=0.30), holding(), LIMITS)
    assert verdict.approved is False
    assert verdict.reason.strip() != ""


def test_buying_back_a_short_is_always_approved():
    """Closing a short is the least risky act available, and the account may
    hold one on a symbol whose shares have since been sold."""
    short = OpenContract(
        occ_symbol="SPY260828P00560000",
        right="P",
        strike=Decimal("560"),
        expiry=date(2026, 8, 28),
        contracts=-4,
        premium=Decimal("2.35"),
    )
    book = holding(shares=0, contracts=[short])
    assert veto(order(contracts=4), book, LIMITS).approved is True


def test_a_purchase_risks_its_premium_and_not_the_strike():
    """Ten 560 puts tie up 560,000 of collateral when sold and cost 2,350 when
    bought. Reading the sold number on a bought order would report a position
    at 187% of a 300,000 account and refuse every hedge the agent proposes."""
    bought = order(contracts=10)
    assert bought.capital_at_risk == Decimal("2350")
    assert veto(bought, holding(), LIMITS).approved is True


def test_a_drawdown_halts_new_risk_but_never_the_defence():
    """The old rule read 'past the limit, no new positions'. Applied to a
    hedge that is exactly backwards: the drawdown is the reason to buy it.

    Selling stays blocked, which is the half that was actually meant.
    """
    # 1,000,000 of equity against a 1,340,000 peak is a 25% drawdown.
    deep = holding(peak_equity=Decimal("1340000"))

    assert veto(order(contracts=10), deep, LIMITS).approved is True

    selling = veto(order(contracts=-1), deep, LIMITS)
    assert selling.approved is False
    assert "drawdown" in selling.reason.lower()


def test_protection_may_reach_flat_but_not_pass_through_it():
    """Hedging to neutral is defence. Hedging past neutral is a short position,
    and it pays only if the market falls -- the bet this agent does not make.

    600,000 of shares against puts carrying -0.90 a share: twenty contracts
    take exposure to -480,000, which is the far side of flat.
    """
    overshoot = veto(order(contracts=20, delta=-0.90), holding(), LIMITS)
    assert overshoot.approved is False
    # Refused on the share count now, which is the earlier and better reason:
    # 1,000 shares can stand behind ten contracts and no more. The directional
    # band would have caught it too, and a rule that only holds because a
    # different rule is watching stops holding when the other one is loosened.
    assert "stand behind" in overshoot.reason.lower()

    # Ten of the same contracts land at +60,000, short of flat, and are fine.
    assert veto(order(contracts=10, delta=-0.90), holding(), LIMITS).approved is True


def test_assignment_probability_is_not_asked_of_a_long():
    """Nobody is assigned on an option they own. Carrying the seller's check
    over to the buyer would reject the deepest protection precisely because it
    is the most likely to pay."""
    assert veto(order(contracts=10, assignment_prob=0.95), holding(), LIMITS).approved


def test_bought_vega_pays_down_the_budget_rather_than_adding_to_it():
    """We are short volatility from writing, so owning some is the cure, not
    more of the disease. Summing magnitudes would have the agent refuse a
    hedge for making its vega worse when the hedge is what fixes it."""
    loaded = holding()
    loaded.vega = 480.0  # LIMITS caps vega at 500
    assert veto(order(contracts=1, vega=40.0), loaded, LIMITS).approved is True
    assert veto(order(contracts=-1, vega=40.0), loaded, LIMITS).approved is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"right": "C"},
        {"assignment_prob": 0.55},
        {"open_interest": 50},
        {"spread_pct": 12.0},
    ],
)
def test_every_rejection_carries_a_non_empty_reason(kwargs):
    verdict = veto(order(**kwargs), portfolio(), LIMITS)
    assert verdict.approved is False
    assert verdict.reason.strip() != ""


def long_put(contracts: int = 3) -> Position:
    """Protection the account already owns, at the strike `order()` proposes."""
    return Position(
        symbol="SPY",
        contracts=[
            OpenContract(
                occ_symbol="SPY260828P00560000",
                right="P",
                strike=Decimal("560"),
                expiry=date(2026, 8, 28),
                contracts=contracts,
                premium=Decimal("2.35"),
            )
        ],
    )


def test_handing_back_a_long_put_is_not_an_uncovered_short():
    """The release path, which this check used to veto for the opposite reason.

    `remedy.closing_orders` prices a handback as a sale, and a sale of a put
    read as an opening short asks for the whole strike in cash: 560 x 3 x 100
    is 168,000, which a client holding their money in shares does not have.
    Nothing is being opened -- the contracts leave the account -- so there is
    no obligation to secure.
    """
    poor = portfolio(cash=Decimal("1000"), positions={"SPY": long_put(3)})
    assert veto(order(contracts=-3), poor, LIMITS).approved is True


def test_selling_more_than_is_held_is_still_collateralised_on_the_surplus():
    """Only the surplus opens a short, and only the surplus is charged for.

    Holding two and selling five gives back two and writes three. Three at 560
    is 168,000 of collateral; approving the whole order because part of it
    closes something would let a naked position through a check written to
    allow a handback.
    """
    holds_two = portfolio(cash=Decimal("1000"), positions={"SPY": long_put(2)})
    refused = veto(order(contracts=-5), holds_two, LIMITS)
    assert refused.approved is False
    assert "cash-secured" in refused.reason

    # Holding two and selling three writes one, and one at 560 is 56,000 --
    # inside both the cash on hand and the concentration cap, so it passes on
    # the surplus rather than on the whole order.
    funded = portfolio(cash=Decimal("200000"), positions={"SPY": long_put(2)})
    assert veto(order(contracts=-3), funded, LIMITS).approved is True


def test_a_written_put_against_nothing_held_is_unchanged():
    """The rule this check exists for still binds when nothing is being closed."""
    poor = portfolio(cash=Decimal("1000"))
    refused = veto(order(contracts=-1), poor, LIMITS)
    assert refused.approved is False
    assert "cash-secured" in refused.reason


def test_a_covered_call_is_not_charged_the_strike_in_cash():
    """The collar's financing leg, which the concentration limit used to refuse.

    `collateral` says it plainly -- "calls are collateralised by shares" -- and
    `_must_not_be_naked` has already refused any call the shares do not cover.
    A call that reaches the concentration check is therefore backed by stock the
    book already holds and the ladder already counts. Charging it the strike as
    well counts that position twice and calls the second copy new risk.

    Twelve contracts at 540 is 648,000, which is 64.8% of this account against a
    25% cap. The put leg of the collar passed and this one did not, so the cycle
    bought the expensive half and was denied the half that pays for it.
    """
    holder = portfolio(
        equity=Decimal("1000000"),
        cash=Decimal("1000000"),
        peak_equity=Decimal("1000000"),
        positions={"SPY": Position(symbol="SPY", leg="SHARES", shares=1200)},
    )
    covered = order(right="C", strike=Decimal("540"), contracts=-12, delta=0.25)
    assert veto(covered, holder, LIMITS).approved is True


def test_a_call_the_shares_do_not_cover_is_still_refused():
    """The check above is safe only because this one runs first."""
    thin = portfolio(
        equity=Decimal("1000000"),
        cash=Decimal("1000000"),
        peak_equity=Decimal("1000000"),
        positions={"SPY": Position(symbol="SPY", leg="SHARES", shares=300)},
    )
    naked = order(right="C", strike=Decimal("540"), contracts=-12, delta=0.25)
    verdict = veto(naked, thin, LIMITS)
    assert verdict.approved is False
    assert "naked call" in verdict.reason


def test_a_written_put_is_still_charged_the_whole_strike():
    """Unchanged, and the reason the call case had to be named rather than the
    whole sale side loosened: an assigned put has to buy the shares in cash."""
    poor = portfolio(equity=Decimal("300000"), cash=Decimal("300000"))
    written = order(right="P", strike=Decimal("560"), contracts=-2)
    verdict = veto(written, poor, LIMITS)
    assert verdict.approved is False
    assert "position size" in verdict.reason


def test_handing_back_a_long_put_is_not_an_assignment_risk():
    """Nobody can be assigned an option they own.

    On 2026-09-02 the client sold their XLF shares, the nine puts behind them
    became redundant, and the order closing them was rejected for an assignment
    probability of 0.41 on a contract the account was long. The limit exists to
    stop the agent writing an option likely to be called away; applied to an
    exit it kept the client in a position they had every reason to leave.
    """
    held = portfolio(
        positions={
            "SPY": Position(
                symbol="SPY",
                leg="PUT_OPEN",
                contracts=[
                    OpenContract(
                        occ_symbol="SPY260828P00560000",
                        right="P",
                        strike=Decimal("560"),
                        expiry=date(2026, 8, 28),
                        contracts=1,
                        premium=Decimal("3.50"),
                    )
                ],
            )
        }
    )
    # One contract, so the only rule with anything to say is the one under
    # test. Nine would also trip the net-delta band, which is a different
    # question and has its own tests.
    closing = order(contracts=-1, assignment_prob=0.41)
    assert veto(closing, held, LIMITS).approved


def test_writing_a_new_option_still_answers_for_assignment():
    """The limit is not removed, only kept off the orders it was never about."""
    naked = order(contracts=-1, assignment_prob=0.41)
    assert not veto(naked, portfolio(), LIMITS).approved
